import json
import re
from collections import defaultdict
from collections.abc import Generator
from copy import deepcopy
from pathlib import Path
from typing import TypeAlias
from datetime import datetime
from loguru import logger
from tenacity import retry, stop_after_attempt
from search.search_manage import SearchManager
from search.search_backend import SearchBackend
from agents.agent_common import InvalidLLMResponse
from data_structures import MessageThread, ReproResult, IssueResult, BugLocation
from log import print_acr, print_reproducer, print_issue_analysis
from model.gpt import common
from task import Task
from rank_bm25 import BM25Okapi
from typing import List, Dict
import os
import numpy as np

SYSTEM_PROMPT = (
    'You are an experienced software engineer responsible for writing a test script for an issue reported in your GitHub project {repo_name}.\n'
    'Do NOT implement any fixes, you are ONLY interested in writing a test script.'
)

SYSTEM_PROMPT_W_RULE = (
    'You are an experienced software engineer responsible for writing a test script for an issue reported in your GitHub project {repo_name}. '
    'Do NOT implement any fixes, you are ONLY interested in writing a test script.\n\n'
    'Here are some experiences drawn from other issues in your project that may provide valuable insights for writing tests for this issue:\n'
    '{summarized_rules}'
)


REPRODUCE_PROMPT = '''
Requirements:
1. Review the existing project test file to understand the conventions, standards, and formats used for test inputs.
2. The script should be **minimal and self-contained**, including only the essential **test inputs** necessary to trigger or expose the issue, along with all required imports, dummy data, and setup to ensure the test inputs are runnable.
3. Each input must be executed individually inside a try-except block to safely catch exceptions and prevent one failure from interrupting the rest of the test inputs. 
4. Print output of each test input following the strict format:
"""
### Test 1:
Input:
<printed input>
Output:
<printed output>

### Test 2:
Input:
<printed input>
Output:
<printed output>

### Test 3:
...
"""

Explain your reasoning first, and then provide the script wrapped with ```python(...)```'''


REPRODUCE_PROMPT_W_EXP = '''
Requirements:
1. Review the existing project test file to understand the conventions, standards, and formats used for test inputs.
2. The script should be **minimal and self-contained**, including only the essential **test inputs** necessary to trigger or expose the issue, along with all required imports, dummy data, and setup to ensure the test inputs are runnable.
3. Each input must be executed individually inside a try-except block to safely catch exceptions and prevent one failure from interrupting the rest of the test inputs. 
4. Print output of each test input following the strict format:
"""
### Test 1:
Input:
<printed input>
Output:
<printed output>

### Test 2:
Input:
<printed input>
Output:
<printed output>

### Test 3:
...
"""

Below is an example test script for another issue:
```python
{retrieved_example}
```

Explain your reasoning first, and then provide the script wrapped with ```python(...)```'''



TEST_INPUTS_PROMPT = '''Here is the reproduction script, along with its execution results when run on the original buggy program:
### Reproduction Script:
```python
{test_content}
```
### Execution Result:
{execution_result}

The reproduction script is intended to reproduce or expose the reported issue. However, it may not be sufficient to verify whether a patch fully resolves the issue without introducing unintended side effects. Your team has developed a set of candidate patches that all pass the reproduction script, but it remains unclear which one is the most reliable.  

Your task is to write a standalone Python script `test.py` that performs **differential testing** by adding multiple diverse **test inputs** beyond the reproduction case.

Requirements:
1. The script must be self-contained. It should follow the same setup (imports, dependencies, object initializations, etc.) as the reproduction script, and include any additional setup necessary to ensure that the new test inputs are runnable.
2. Develop Test Inputs:
   - Review the existing project test file to understand the conventions, standards, and formats used for test inputs.
   - Include up to 8 additional test inputs related to the issue, covering a variety of scenarios such as edge cases and issue-specific regression risks, to ensure comprehensive coverage.
   - You are encouraged to select relevant test inputs from existing test files to prevent regressions.
3. Each input must be executed individually inside a try-except block to safely catch exceptions and prevent one failure from interrupting the rest of the test inputs. 
4. Print output of each test input following the strict format:
"""
### Test 1:
Input:
<printed input>
Output:
<printed output>

### Test 2:
Input:
<printed input>
Output:
<printed output>

### Test 3:
...
"""

Explain your reasoning first, and then provide the script wrapped with ```python(...)```'''

TEST_INPUTS_PROMPT_WO_REPRODUCTION = '''Your task is to write a standalone Python script `test.py` that performs **differential testing** by including multiple diverse **test inputs**.

Requirements:
1. Develop Test Inputs:
   - Review the existing project test file to understand the conventions, standards, and formats used for test inputs.
   - Include up to 8 test inputs related to the issue, covering a variety of scenarios such as edge cases and issue-specific regression risks, to ensure comprehensive coverage.
   - You are encouraged to select relevant test inputs from existing test files to prevent regressions.
2. The script should be **self-contained**, including all required imports, dummy data, and setup to ensure the test inputs are runnable.
3. Each input must be executed individually inside a try-except block to safely catch exceptions and prevent one failure from interrupting the rest of the test inputs. 
4. Print output of each test input following the strict format:
"""
### Test 1:
Input:
<printed input>
Output:
<printed output>

### Test 2:
Input:
<printed input>
Output:
<printed output>

### Test 3:
...
"""

Explain your reasoning first, and then provide the script wrapped with ```python(...)```'''



class NoReproductionStep(RuntimeError):
    """Raised when issue statement does not contain steps for reproduction."""

    pass


class NoNeedReproduction(RuntimeError):
    """Raised when issue statement does not contain steps for reproduction."""

    pass


TestHandle: TypeAlias = str


class TestAgent:
    def __init__(self, task: Task, task_dir: str, repro_result_dict, original_test_file) -> None:
        self.task = task
        self.task_dir = task_dir
        self.repro_result_dict = repro_result_dict
        self.original_test_file = original_test_file
        self.backend = SearchBackend(task.project_path)

        self._request_idx: int = -1
        self._responses: dict[TestHandle, str] = {}
        self._tests: dict[TestHandle, str] = {}
        self._feedbacks: dict[TestHandle, list[str]] = defaultdict(list)
        self._history: list[TestHandle] = []
        self._non_repro_history: list[TestHandle] = []
        # add by mfw
        self._context: dict[TestHandle, list[str]] = defaultdict(list)

    def info_set_up(self, meta_data):
        self._request_idx = meta_data['_request_idx']

        if meta_data['_responses']:
            self._responses = meta_data['_responses']
        if meta_data['_tests']:
            self._tests = meta_data['_tests']
        if meta_data['_feedbacks']:
            self._feedbacks = meta_data['_feedbacks']
        if meta_data['_history']:
            self._history = meta_data['_history']
        if meta_data['_non_repro_history']:
            self._non_repro_history = meta_data['_non_repro_history']
        # add by mfw
        if meta_data['_context']:
            self._context = meta_data['_context']

    def write_reproducing_test(
            self, retries: int = 3
    ) -> tuple[TestHandle, str, ReproResult]:
        return self._write_reproducing_test(num_feedbacks=1, retries=retries)

    def write_reproducing_test_W_EXP(
            self, retries: int = 4
    ) -> tuple[TestHandle, str, ReproResult]:
        return self._write_reproducing_test_W_EXP(num_feedbacks=1, retries=retries)


    def add_feedback(self, handle: TestHandle, feedback: str) -> None:
        if handle not in self._tests:
            raise ValueError("patch {} does not exist", handle)

        if handle in self._feedbacks:
            self._feedbacks[handle].append(feedback)
        else:
            self._feedbacks[handle] = [feedback]


    def _write_reproducing_test(
        self, num_feedbacks: int, retries: int
    ):
        experiences = []
        exp_path = Path(self.task_dir, f"reproduce_experiences.jsonl")
        test_content = ""
        repro_result = ""
        reproducible = ""
        initial_thread = None
        for _ in range(retries):
            old_test = test_content
            old_exec_result = repro_result
            old_check_repro = reproducible

            feedback_handles = self._select_feedback_handles(num_feedbacks)

            response, test_content, thread = self._call_api_reproduce(feedback_handles)
            self._request_idx += 1
            print_reproducer(response)
            Path(self.task_dir, f"reproduce_raw_{self._request_idx}.md").write_text(response)
            thread.save_to_file(
                Path(self.task_dir, f"conv_reproduce_{self._request_idx}.json")
            )

            if test_content is None:
                test_content = old_test
                repro_result = old_exec_result
                reproducible = old_check_repro
                continue

            repro_result = self.task.execute_test(test_content)
            print_acr(str(repro_result))

            # assert 1 == 2

            if initial_thread is None:
                initial_thread = deepcopy(thread)

            reproducible, guard_thread = self._reproduction_is_correct(initial_thread, test_content, repro_result)

            # todo get and save experience
            cur_exp = {
                # "issue_description": self.task.get_issue_statement().strip(),
                "old_test": old_test,
                "old_exec_result": old_exec_result.stdout.strip() + '\n' + old_exec_result.stderr.strip() if old_exec_result != "" else old_exec_result,
                "old_returncode": old_exec_result.returncode if old_exec_result != "" else old_exec_result,
                "old_check_repro": old_check_repro,
                "new_test": test_content,
                "new_exec_result": repro_result.stdout.strip() + '\n' + repro_result.stderr.strip(),
                "new_returncode": repro_result.returncode,
                "new_check_repro": reproducible
            }

            experiences.append(cur_exp)

            # with open(exp_path, "a") as ff:
            #     ff.write(json.dumps(cur_exp) + "\n")

            # todo saving the reproducible result
            print_reproducer(
                reproducible["test-analysis"] +
                '\n\nif-reproduce: ' + reproducible['if-reproduce'] +
                '\n\ntest-advice: ' + reproducible['test-advice'])
            guard_thread.save_to_file(
                Path(self.task_dir, f"conv_reproduce_correct_{self._request_idx}.json"))

            if_reproduced = reproducible.get("if-reproduce", "")

            if if_reproduced == 'YES':
                handle = self._register_reproducing_test(response, test_content)

                # todo saving the experiences
                cur_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(exp_path, "a") as ff:
                    ff.write(json.dumps(
                        {'time': cur_time,
                         "issue_description": self.task.get_issue_statement().strip(),
                         'exps': experiences}
                    ) + "\n")

                return handle, test_content, repro_result

            handle = self._register_non_reproducing_test_final(
                response, test_content, repro_result,
                reproducible.get("test-analysis", None),
                reproducible.get("test-advice", None)
            )
            logger.info("registered non reproducing test {}", handle)

        # raise InvalidLLMResponse(
        #     f"Failed to write a reproducing test in {retries} attempts"
        # )
        print(f"Failed to write a reproducing test in {retries} attempts")

        # todo saving the experiences
        cur_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(exp_path, "a") as ff:
            ff.write(json.dumps(
                {'time': cur_time,
                 "issue_description": self.task.get_issue_statement().strip(),
                 'exps': experiences}
            ) + "\n")

        return '', '', None


    def _write_reproducing_test_W_EXP(
        self, num_feedbacks: int, retries: int
    ):
        experiences = []
        exp_path = Path(self.task_dir, f"reproduce_experiences.jsonl")
        test_content = ""
        repro_result = ""
        reproducible = ""
        initial_thread = None
        for _ in range(retries):
            old_test = test_content
            old_exec_result = repro_result
            old_check_repro = reproducible

            feedback_handles = self._select_feedback_handles(num_feedbacks)

            if test_content == "" or repro_result == "":
                ########## todo retrieve experiences and add them to feedback
                retrieved_exps, sim_scores = get_experiences(
                    self.task.get_issue_statement(), "", "",
                    self.task.repo_name.split('/')[-1], self.task_dir,
                    exp_name='reproduce_experiences'
                )
                if retrieved_exps:
                    retrieved_example = retrieved_exps[0]['new_test'].strip()
                else:
                    retrieved_example = "Not available"

                response, test_content, thread = self._call_api_reproduce_exp_initial(feedback_handles,
                                                                                             retrieved_example)
            else:
                response, test_content, thread = self._call_api_reproduce(feedback_handles)

            self._request_idx += 1
            print_reproducer(response)
            Path(self.task_dir, f"reproduce_raw_{self._request_idx}.md").write_text(response)
            thread.save_to_file(
                Path(self.task_dir, f"conv_reproduce_{self._request_idx}.json")
            )

            if test_content is None:
                test_content = old_test
                repro_result = old_exec_result
                reproducible = old_check_repro
                continue

            repro_result = self.task.execute_test(test_content)
            print_acr(str(repro_result))

            # assert 1 == 2

            if initial_thread is None:
                initial_thread = deepcopy(thread)

            reproducible, guard_thread = self._reproduction_is_correct(initial_thread, test_content, repro_result)

            # todo get and save experience
            cur_exp = {
                # "issue_description": self.task.get_issue_statement().strip(),
                "old_test": old_test,
                "old_exec_result": old_exec_result.stdout.strip() + '\n' + old_exec_result.stderr.strip() if old_exec_result != "" else old_exec_result,
                "old_returncode": old_exec_result.returncode if old_exec_result != "" else old_exec_result,
                "old_check_repro": old_check_repro,
                "new_test": test_content,
                "new_exec_result": repro_result.stdout.strip() + '\n' + repro_result.stderr.strip(),
                "new_returncode": repro_result.returncode,
                "new_check_repro": reproducible
            }

            experiences.append(cur_exp)

            # with open(exp_path, "a") as ff:
            #     ff.write(json.dumps(cur_exp) + "\n")

            # todo saving the reproducible result
            print_reproducer(
                reproducible["test-analysis"] +
                '\n\nif-reproduce: ' + reproducible['if-reproduce'] +
                '\n\ntest-advice: ' + reproducible['test-advice'])
            guard_thread.save_to_file(
                Path(self.task_dir, f"conv_reproduce_correct_{self._request_idx}.json"))

            if_reproduced = reproducible.get("if-reproduce", "")

            if if_reproduced == 'YES':
                handle = self._register_reproducing_test(response, test_content)

                # todo saving the experiences
                cur_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(exp_path, "a") as ff:
                    ff.write(json.dumps(
                        {'time': cur_time,
                         "issue_description": self.task.get_issue_statement().strip(),
                         'exps': experiences}
                    ) + "\n")

                return handle, test_content, repro_result

            ########## todo retrieve experiences and add them to feedback
            retrieved_exps, sim_scores = get_experiences(
                self.task.get_issue_statement(),
                test_content,
                repro_result.stdout.strip() + '\n' + repro_result.stderr.strip(),
                self.task.repo_name.split('/')[-1], self.task_dir,
                exp_name='reproduce_experiences'
            )

            handle = self._register_non_reproducing_test_final_W_EXP(
                response, test_content, repro_result,
                reproducible.get("test-analysis", None),
                reproducible.get("test-advice", None),
                retrieved_exps
            )
            logger.info("registered non reproducing test {}", handle)

        # raise InvalidLLMResponse(
        #     f"Failed to write a reproducing test in {retries} attempts"
        # )
        print(f"Failed to write a reproducing test in {retries} attempts")

        # todo saving the experiences
        cur_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(exp_path, "a") as ff:
            ff.write(json.dumps(
                {'time': cur_time,
                 "issue_description": self.task.get_issue_statement().strip(),
                 'exps': experiences}
            ) + "\n")

        return '', '', None


    def _write_test_inputs_w_reproduction(
        self, test_content, orig_repro_result, test_nums = 3,
    ):
        response_list, test_content_list, thread = self._call_api_write_test_inputs_w_reproduction(
            test_content, orig_repro_result, test_nums
        )

        for res in response_list:
            thread.add_model(res)

        thread.save_to_file(
            Path(self.task_dir, f"conv_{test_nums}_verified_test.json")
        )

        for test_idx, test_content in enumerate(test_content_list):
            if test_content:
                Path(self.task_dir, f"verified_test_{test_idx}.py").write_text(test_content)

        return test_content_list


    def _write_test_inputs_wo_reproduction(
        self, test_nums = 3,
    ):
        response_list, test_content_list, thread = self._call_api_write_test_inputs_wo_reproduction(
            test_nums
        )

        for res in response_list:
            thread.add_model(res)

        thread.save_to_file(
            Path(self.task_dir, f"conv_{test_nums}_verified_test.json")
        )

        for test_idx, test_content in enumerate(test_content_list):
            if test_content:
                Path(self.task_dir, f"verified_test_{test_idx}.py").write_text(test_content)

        return test_content_list


    def _reproduction_is_correct(
            self, initial_thread, test_content, repro_result
    ):
        prefix_thread = deepcopy(initial_thread)
        prefix_thread.add_model(f"### Test Script:\n```python\n{test_content}\n```")

        reproducer_prompt = ("The above test script, including only test inputs, was designed to expose the reported issue. It has been executed on the original buggy program before applying any patch. "
                             "The execution results are as follows:\n")
        reproducer_prompt += ("### Stdout:\n" + repro_result.stdout.strip() + '\n' +
                              '### Stderr:\n' + repro_result.stderr.strip() + '\n\n###\n\n')

        reproducer_prompt += (
            "Please review the issue description, the test script, and the execution results. Then, assess (1) whether the script clearly prints each test input and its corresponding output; "
            "(2) whether each test output behaves as expected for its intended purpose: "
            "either reproducing the symptoms, exceptions, or "
            "faulty behaviors explicitly described in the issue description (if designed to do so), or executing successfully without unrelated errors (if not intended to trigger the issue).\n\n"
        )

        reproducer_prompt += """Explain your reasoning, and then provide the response in the following format:

### Output Format:
<test_analysis>...</test_analysis>
<test_correct>[YES|NO]</test_correct>
<test_advice>...</test_advice>

Field Descriptions:
1. In the "test_analysis" field, provide a detailed analysis of the test script's execution outcomes, explaining which parts (if any) expose the issue, which fail due to unrelated errors, and the implications.
2. In the "test_correct" field:
   - Indicate "YES" if (1) the script prints all test inputs and outputs clearly; and (2) test outputs behave as expected for its intended purpose.
   - Otherwise, indicate "NO" if any test input produces unrelated errors (e.g., missing dependencies, setup errors, irrelevant exceptions) or fails to reflect the issue's described symptoms.
3. In the "test_advice" field:
   - If "test_correct" is "YES", leave this field empty.
   - If "test_correct" is "NO", provide clear, actionable suggestions for improving or correcting the test script.

Note: The current runtime environment and its package versions cannot be modified, and no additional packages can be installed. If certain packages are unavailable, the script should focus on confirming or exposing the issue without relying on those missing packages.
"""

        prefix_thread.add_user(reproducer_prompt)
        print_acr(reproducer_prompt)

        # @retry(stop=stop_after_attempt(3))
        def query_and_parse():
            for _ in range(3):
                response, *_ = common.GPTo4_MODEL.call(
                    prefix_thread.to_msg()
                )

                print(response)
                result = ase_extract_result(response)

                if not isinstance(result, dict):
                    print("InvalidLLMResponse")
                    continue

                thread = deepcopy(prefix_thread)
                thread.add_model(response)

                return result, thread

            raise InvalidLLMResponse

        return query_and_parse()


    def _select_feedback_handles(self, max_num_feedbacks: int) -> list[TestHandle]:
        if 0 <= max_num_feedbacks <= len(self._history):
            return self._history[-max_num_feedbacks:]
        elif max_num_feedbacks <= len(self._history) + len(self._non_repro_history):
            num_non_repro = max_num_feedbacks - len(self._history)
            return [
                *self._non_repro_history[-num_non_repro:],
                *self._history,
            ]
        else:
            return [*self._non_repro_history, *self._history]


    def _call_api_reproduce(
            self, history_handles: list[TestHandle] | None = None
    ) -> tuple[str, str | None, MessageThread]:
        history_handles = history_handles or []

        thread = self._construct_init_thread()

        if self.original_test_file:
            file_content = self.backend.get_file_content(self.original_test_file)[0]
            file_content = '\n'.join(file_content.split('\n')[:1000])

            regression_prompt = "Here is an existing project test file:\n"
            regression_prompt += "```\n" + file_content.strip() + "\n```"
            thread.add_user(regression_prompt)

        prefix_prompt = (
            "Please analyze the issue description to understand the core problem. Based on your analysis, write a standalone Python script `test.py` to reproduce the issue. "
            "The script will be put in the root directory of the project and executed by `python3 test.py`.")

        thread.add_user(prefix_prompt + REPRODUCE_PROMPT)
        if not history_handles:
            print_acr(prefix_prompt + REPRODUCE_PROMPT)

        for handle in history_handles:
            if feedbacks := self._feedbacks.get(handle, []):
                thread.add_model(self._responses[handle], [])
                for feedback in feedbacks:
                    thread.add_user(feedback)

                prefix_prompt = "Review the test script you have written and its execution result. Then, incorporating the suggestions, write a correct test script to reproduce the issue."
                thread.add_user(prefix_prompt + REPRODUCE_PROMPT)
                print_acr(prefix_prompt + REPRODUCE_PROMPT)
            else:
                logger.warning("test {} does not have a feedback; skipping", handle)

        response, *_ = common.SELECTED_MODEL.call(thread.to_msg())

        return response, self.convert_response_to_test(response), thread


    def _call_api_reproduce_exp_initial(
            self, history_handles: list[TestHandle] | None = None, retrieved_example = None
    ) -> tuple[str, str | None, MessageThread]:
        history_handles = history_handles or []

        thread = self._construct_init_thread()

        if self.original_test_file:
            file_content = self.backend.get_file_content(self.original_test_file)[0]
            file_content = '\n'.join(file_content.split('\n')[:1000])

            regression_prompt = "Here is an existing project test file:\n"
            regression_prompt += "```\n" + file_content.strip() + "\n```"
            thread.add_user(regression_prompt)

        prefix_prompt = (
            "Please analyze the issue description to understand the core problem. Based on your analysis, write a standalone Python script `test.py` to reproduce the issue. "
            "The script will be put in the root directory of the project and executed by `python3 test.py`.")

        thread.add_user(prefix_prompt + REPRODUCE_PROMPT_W_EXP.format(retrieved_example=retrieved_example))
        if not history_handles:
            print_acr(prefix_prompt + REPRODUCE_PROMPT_W_EXP.format(retrieved_example=retrieved_example))

        response, *_ = common.SELECTED_MODEL.call(thread.to_msg())

        return response, self.convert_response_to_test(response), thread


    def _call_api_write_test_inputs_w_reproduction(
            self, test_content, orig_repro_result, test_nums
    ):
        thread = self._construct_init_thread()

        if self.original_test_file:
            file_content = self.backend.get_file_content(self.original_test_file)[0]
            file_content = '\n'.join(file_content.split('\n')[:1000])

            regression_prompt = "Here is an existing project test file:\n"
            regression_prompt += "```\n" + file_content.strip() + "\n```"
            thread.add_user(regression_prompt)

        # reproduction_prompt = "Below is the reproduction script, along with its execution results when run on the original buggy program:\n"
        # reproduction_prompt += f'### Reproduction Script:\n```python\n{test_content}\n```\n'
        # reproduction_prompt += f'### STDOUT:\n{orig_repro_result.stdout.strip()}\n### STDERR:\n{orig_repro_result.stderr.strip()}'
        # # if correct_patch_list is None or correct_patched_repro is None:
        # thread.add_user(reproduction_prompt)

        execution_result = orig_repro_result.stdout.strip() + '\n' + orig_repro_result.stderr.strip()

        thread.add_user(TEST_INPUTS_PROMPT.format(test_content=test_content.strip(), execution_result=execution_result))
        print_acr(TEST_INPUTS_PROMPT)

        response_list, test_content_list = [], []

        # t1_nums = test_nums // 2
        t_list = [0.0] + [0.8] * (test_nums - 1)
        assert len(t_list) == test_nums

        for cur_temperature in t_list:
            response, *_ = common.SELECTED_MODEL.call(thread.to_msg(), temperature=cur_temperature)
            print_reproducer(response)
            test_content = self.convert_response_to_test(response)

            if test_content:
                response_list.append(response)
                test_content_list.append(test_content)

        return response_list, test_content_list, thread

    def _call_api_write_test_inputs_wo_reproduction(
            self, test_nums
    ):
        thread = self._construct_init_thread()

        if self.original_test_file:
            file_content = self.backend.get_file_content(self.original_test_file)[0]
            file_content = '\n'.join(file_content.split('\n')[:1000])

            regression_prompt = "Here is an existing project test file:\n"
            regression_prompt += "```\n" + file_content.strip() + "\n```"
            thread.add_user(regression_prompt)

        thread.add_user(TEST_INPUTS_PROMPT_WO_REPRODUCTION)
        print_acr(TEST_INPUTS_PROMPT_WO_REPRODUCTION)

        response_list, test_content_list = [], []

        # t1_nums = test_nums // 2
        t_list = [0.0] + [0.8] * (test_nums - 1)
        assert len(t_list) == test_nums

        for cur_temperature in t_list:
            response, *_ = common.SELECTED_MODEL.call(thread.to_msg(), temperature=cur_temperature)
            print_reproducer(response)
            test_content = self.convert_response_to_test(response)

            if test_content:
                response_list.append(response)
                test_content_list.append(test_content)

        return response_list, test_content_list, thread


    def _construct_init_thread(self) -> MessageThread:
        thread = MessageThread()
        thread.add_system(SYSTEM_PROMPT.format(repo_name=self.task.repo_name))

        thread.add_user("Here is the issue description:\n"
                        "<issue>\n" + self.task.get_issue_statement().strip() + "\n</issue>")

        return thread

    def _construct_init_thread_exp(self, tester_exps) -> MessageThread:
        thread = MessageThread()
        thread.add_system(SYSTEM_PROMPT_W_RULE.format(repo_name=self.task.repo_name,
                                                      summarized_rules=tester_exps))

        thread.add_user("<issue>\n" + self.task.get_issue_statement().strip() + "\n</issue>")

        return thread


    def _register_reproducing_test(
            self, response: str, test_content: str, search_context=None,
    ) -> TestHandle:
        handle = str(self._request_idx)

        assert handle not in self._responses
        assert handle not in self._feedbacks
        assert handle not in self._tests
        assert handle not in self._history
        assert handle not in self._context

        self._responses[handle] = response
        self._tests[handle] = test_content
        self._history.append(handle)
        if search_context is not None:
            self._context[handle] = search_context

        return handle


    def _register_non_reproducing_test_final(
            self, response: str, test_content: str, repro_result: ReproResult, analysis, advice
    ) -> TestHandle:
        handle = str(self._request_idx)

        assert handle not in self._responses
        assert handle not in self._feedbacks
        assert handle not in self._tests
        assert handle not in self._non_repro_history

        self._responses[handle] = response
        self._tests[handle] = test_content
        self._non_repro_history.append(handle)
        self._feedbacks[handle].append(self._feedback_from_repro_result_final(repro_result, analysis, advice))

        return handle


    def _register_non_reproducing_test_final_W_EXP(
            self, response: str, test_content: str, repro_result: ReproResult, analysis, advice, experiences
    ) -> TestHandle:
        handle = str(self._request_idx)

        assert handle not in self._responses
        assert handle not in self._feedbacks
        assert handle not in self._tests
        assert handle not in self._non_repro_history

        self._responses[handle] = response
        self._tests[handle] = test_content
        self._non_repro_history.append(handle)
        self._feedbacks[handle].append(self._feedback_from_repro_result_final_W_EXP(repro_result,
                                                                                    analysis, advice, experiences))

        return handle


    def _feedback_from_repro_result_final(self, repro_result: ReproResult, analysis, advice) -> str:
        return (
            "The following results were obtained by executing the test script on the original buggy program:\n"
            f"### Execution Results:\n{repro_result.stdout.strip()}\n{repro_result.stderr.strip()}\n"
            f'### Analysis:\n{analysis.strip()}\n\nAs a result, the test script failed to reproduce the issue.\n\n'
            f'### Suggestions for correcting the test script:\n{advice.strip()}'
        )

    def _feedback_from_repro_result_final_W_EXP(self, repro_result: ReproResult, analysis, advice, experiences) -> str:
        prompt = (
            "The following results were obtained by executing the test script on the original buggy program:\n"
            f"### Execution Results:\n{repro_result.stdout.strip()}\n{repro_result.stderr.strip()}\n"
            f'### Analysis:\n{analysis.strip()}\n\nAs a result, the test script failed to reproduce the issue.\n\n'
            f'### Suggestions for correcting the test script:\n{advice.strip()}\n\n###\n\n'
            f'When writing test scripts for other issues, you met some similar errors, but you finally addressed them. Here are some examples:\n'
        )

        for idx, cur_exp in enumerate(experiences[:1]):
            prompt += f"=== Example {idx+1} ===\n"
            # prompt += ('### Execution Results of the Incorrect Script\n' + preprocess_traceback(cur_exp['old_exec_result'].strip()) + '\n' +
            #            '### Correct Script:\n```python\n' + cur_exp['new_test'].strip() + '\n```')
            prompt += ("### Incorrect Script:\n```python\n" + cur_exp['old_test'].strip() + '\n```\n' +
                       # '### Execution Result:\n' + preprocess_traceback(cur_exp['old_exec_result'].strip()) + '\n\n' +
                       '### Correct Script:\n```python\n' + cur_exp['new_test'].strip() + '\n```')
            prompt += "\n\n"


        return prompt.strip()


    @classmethod
    def convert_response_to_test(cls, response: str) -> str | None:
        blocks = extract_markdown_code_blocks(response)

        if len(blocks) == 1:
            return blocks[0]
        elif len(blocks) == 2 and blocks[1].strip() == "python3 reproducer.py":
            return blocks[0]
        elif len(blocks) >= 2 and response.split('```')[1][:6] == "python":
            return blocks[0]
        else:
            return None

    @classmethod
    def convert_response_to_patch(cls, response: str) -> str | None:
        blocks = extract_markdown_code_blocks(response)

        if len(blocks) == 1:
            return blocks[0]
        elif len(blocks) >= 2 and blocks[1].strip() == "python3 test_patch.py":
            return blocks[0]
        else:
            return None

    def save_test(self, handle: TestHandle) -> None:
        Path(self.task_dir, f"reproducer_{handle}.py").write_text(self._tests[handle])


def extract_tests(input_str):
    import re
    """
    Extract all patches (### Patch X followed by a code block) from the given string.

    Parameters:
        input_str (str): The input string containing patches.

    Returns:
        dict: A dictionary mapping patch numbers to their corresponding content.
    """
    patches = []

    # Regex pattern to capture "### Patch X:" followed by a code block
    pattern = re.findall(r"```python(.*?)```", input_str, re.DOTALL)

    for content in pattern:
        patches.append(content.strip())

    if not patches:
        pattern = re.findall(r"```(.*?)```", input_str, re.DOTALL)

        for content in pattern:
            patches.append(content.strip())

    if not patches:
        print(input_str)
        return None

    return patches[-1]


def extract_markdown_code_blocks(content: str) -> list[str]:
    lines = content.splitlines(keepends=True)

    in_code_block = False
    start_pattern = r"\s*```\w*\s*"
    end_pattern = r"\s*```\s*"

    start, end = -1, -1
    intervals = []

    for idx, line in enumerate(lines):
        if (not in_code_block) and re.match(start_pattern, line):
            in_code_block = True
            start = idx + 1
        elif in_code_block and re.match(end_pattern, line):
            in_code_block = False
            end = idx
            intervals.append((start, end))

    res = ["".join(lines[start:end]) for start, end in intervals]
    return res


def get_experiences(issue_description, test_content, repro_result, repo_name, task_dir, exp_name):
    # Load and preprocess the knowledge base
    knowledge_base_file = save_experiences(repo_name, task_dir, issue_description, exp_name)

    if test_content == "" or repro_result == "":
        if_initial = True
    else:
        if_initial = False

    filter_kb = load_knowledge_base(knowledge_base_file, if_initial)
    print("=========================== num of available experiences", len(filter_kb), "==============================")

    if len(filter_kb) == 0:
        return [], 0

    # Build BM25 indices for specific fields
    bm25_indices = build_bm25_index(filter_kb, if_initial)

    if if_initial:
        weights = {
            "issue_description": 0.6,
            "new_test": 0.4
        }

        query = {
            "issue_description": preprocess_text(issue_description),
            "new_test": preprocess_text(issue_description),
        }
    else:
        weights = {
            "old_exec_result": 0.9,
            "old_test": 0.1
        }

        query = {
            "old_exec_result": preprocess_text(preprocess_traceback(repro_result)),
            "old_test": preprocess_text(test_content)
        }

    # Retrieve examples using weighted scores
    topk_exps, topk_scores = retrieve_examples_with_weights(query, bm25_indices, filter_kb, weights, top_k=10)
    # print('topk_scores', topk_scores)

    prompt_num = 3
    prompt_exps, prompt_scores = [topk_exps[0]], [topk_scores[0]]
    for temp_exp, temp_score in zip(topk_exps[1:], topk_scores[1:]):
        assert len(prompt_exps) == len(prompt_scores)
        if len(prompt_exps) == prompt_num:
            break

        repeat_flag = False
        for exist_exp in prompt_exps:
            if temp_exp['issue_description'].strip() == exist_exp['issue_description'].strip():
                repeat_flag = True
                break

        if not repeat_flag:
            prompt_exps.append(temp_exp)
            prompt_scores.append(temp_score)

    return prompt_exps, prompt_scores


def load_knowledge_base(file_path: str, if_initial):
    """Load knowledge base from JSONL file."""
    filter_kb = []
    with open(file_path, 'r') as f:
        for line in f:
            example = json.loads(line)
            if if_initial:
                if example['old_test'] == "" and example['new_check_repro']['if-reproduce'] == "YES":
                    filter_kb.append(example)
            else:
                if example['old_test'] != "" and example['new_check_repro']['if-reproduce'] == "YES":
                    filter_kb.append(example)

    return filter_kb

def preprocess_knowledge_base(file_path: str) -> List[Dict]:
    """Preprocess knowledge base from JSONL file."""
    preprocessed_kb = []
    with open(file_path, 'r') as f:
        for line in f:
            example = json.loads(line)
            example['issue_description'] = preprocess_text(example['issue_description'])
            example['old_test'] = preprocess_text(example['old_test'])
            example['old_exec_result'] = preprocess_text(example['old_exec_result'])
            preprocessed_kb.append(example)
    return preprocessed_kb


def preprocess_traceback(traceback: str):
    traceback = ase_remove_summary_block(traceback)

    if 'Traceback' not in traceback:
        return traceback

    lines = traceback.strip().split("\n")

    # Extract the most relevant frame (last one before the exception)
    relevant_frame = None
    for line in reversed(lines):
        if line.strip().startswith("File"):
            relevant_frame = line.strip()
            break

    # Parse the relevant frame
    frame_info = ""
    if relevant_frame:
        frame_match = re.match(
            r'^File "(.*?)", line (\d+), in (.+)$', relevant_frame
        )
        if frame_match:
            # print(relevant_frame)
            frame_info =relevant_frame

    if frame_info == "":
        return traceback
    else:
        # return frame_info + traceback.split(frame_info)[-1]
        return traceback.split(frame_info)[-1]


def preprocess_text(text: str) -> List[str]:
    """Tokenize and preprocess text (lowercase, remove punctuation)."""
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)  # Remove punctuation
    text_split = text.split()
    text = ' '.join(text_split)
    return text.split()


def build_bm25_index(kb: List[Dict], if_initial):
    """Build BM25 index for a specific field from preprocessed JSONL knowledge base."""
    if if_initial:
        # initial prompt just consider issue_description
        corpus = [preprocess_text(example['issue_description']) for example in kb]
        test_corpus = [preprocess_text(example['new_test']) for example in kb]
        return {
            'issue_description': BM25Okapi(corpus),
            'new_test': BM25Okapi(test_corpus)
        }
    else:
        # feedback prompt consider execution_result and test_content
        test_corpus = [preprocess_text(example['old_test']) for example in kb]
        exec_corpus = [preprocess_text(preprocess_traceback(example['old_exec_result'])) for example in kb]
        return {
            'old_test': BM25Okapi(test_corpus),
            'old_exec_result': BM25Okapi(exec_corpus)
        }


def retrieve_examples_with_weights(
    preprocessed_queries,
    bm25_indices,
    kb,
    weights,
    top_k,
):
    """
    Retrieve top-k examples from the knowledge base using weighted BM25 scores.

    Args:
        query (Dict[str, str]): Query fields (e.g., {"execution_results": "...", "issue_description": "..."}).
        bm25_indices (Dict[str, BM25Okapi]): Pre-built BM25 indices for each field.
        kb (List[Dict]): Knowledge base.
        weights (Dict[str, float]): Weights for each field.
        top_k (int): Number of top results to retrieve.

    Returns:
        List[Dict]: Top-k examples from the knowledge base.
    """
    # Initialize a list to store combined scores for all examples
    combined_scores = [0.0] * len(kb)

    # Compute weighted scores for each field
    for field, weight in weights.items():
        if field in preprocessed_queries and field in bm25_indices:
            field_scores = bm25_indices[field].get_scores(preprocessed_queries[field])
            score_norm = (field_scores - np.min(field_scores)) / (np.max(field_scores) - np.min(field_scores) + 1e-8)

            combined_scores = [cs + weight * fs for cs, fs in zip(combined_scores, score_norm)]

            # print(field, weight)
            # print(field_scores)
            # print(score_norm)
            # print('-----------')

    # Get the top-k examples based on combined scores
    top_indices = sorted(range(len(combined_scores)), key=lambda i: combined_scores[i], reverse=True)[:top_k]

    topk_exps = [kb[i] for i in top_indices]
    topk_scores = [combined_scores[i] for i in top_indices]
    return topk_exps, topk_scores


def retrieve_examples(query: str, bm25: BM25Okapi, kb: List[Dict], field: str, top_k: int = 5):
    """Retrieve top-k examples from the knowledge base."""
    if '_exec_result' in field:
        preprocessed_query = preprocess_text(preprocess_traceback(query))
    else:
        preprocessed_query = preprocess_text(query)

    scores = bm25.get_scores(preprocessed_query)
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    return [kb[i] for i in top_indices], [scores[i] for i in top_indices]

def filter_returncode_experiences(issue_description, experiences):
    """
    Filters successful experiences by considering each entry's `new_returncode` and the following entries in sequence.

    Parameters:
        experiences (list): A list of experience dictionaries.

    Returns:
        list: A list of successful experiences.
    """
    successful_experiences = []

    for i in range(len(experiences)):
        old_returncode = experiences[i].get("old_returncode", "")
        new_returncode = experiences[i].get("new_returncode", "")

        # Check the current entry's old_returncode
        if old_returncode == 1:
            # Check if any subsequent entry has new_returncode == 0
            for j in range(i, len(experiences)):  # Start from the current entry
                if (experiences[j].get("new_returncode", "") == 0 and
                        ('Issue exists' in experiences[j].get("new_exec_result", "") or 'Issue is resolved' in
                         experiences[j].get("new_exec_result", ""))):
                    successful_experiences.append({
                        "issue_description": issue_description,
                        "old_test": experiences[i]["old_test"],
                        "old_exec_result": experiences[i]["old_exec_result"],
                        "old_returncode": experiences[i]["old_returncode"],
                        "old_check_repro": experiences[i]["old_check_repro"],
                        "new_test": experiences[j]["new_test"],
                        "new_exec_result": experiences[j]["new_exec_result"],
                        "new_returncode": experiences[j]["new_returncode"],
                        "new_check_repro": experiences[j]["new_check_repro"]
                    })
                    break  # Stop searching once a match is found for the current entry

    return successful_experiences


def filter_successful_experiences(issue_description, experiences):
    """
    Filters successful experiences by considering each entry's `new_returncode` and the following entries in sequence.

    Parameters:
        experiences (list): A list of experience dictionaries.

    Returns:
        list: A list of successful experiences.
    """
    successful_experiences = []

    for i in range(len(experiences)):
        old_check_repro = experiences[i].get("old_check_repro", {})
        old_correct = old_check_repro["if-reproduce"] if old_check_repro != "" else None

        # Check the current entry's old_returncode
        if old_correct == "NO" or experiences[i]["old_test"] == "":
            # Check if any subsequent entry has new_returncode == 0
            for j in range(i, len(experiences)):  # Start from the current entry
                new_check_repro = experiences[j].get("new_check_repro", {})
                new_correct = new_check_repro["if-reproduce"]
                if new_correct == "YES":
                    successful_experiences.append({
                        "issue_description": issue_description,
                        "old_test": experiences[i]["old_test"],
                        "old_exec_result": experiences[i]["old_exec_result"],
                        "old_returncode": experiences[i]["old_returncode"],
                        "old_check_repro": experiences[i]["old_check_repro"],
                        "new_test": experiences[j]["new_test"],
                        "new_exec_result": experiences[j]["new_exec_result"],
                        "new_returncode": experiences[j]["new_returncode"],
                        "new_check_repro": experiences[j]["new_check_repro"]
                    })
                    break  # Stop searching once a match is found for the current entry

    return successful_experiences


def save_experiences(repo_name, task_dir, issue_description, exp_name):
    exp_directory = '/'.join(task_dir.split('/')[:-1])
    print('test_exp_dir:', exp_directory)
    total_exps = []
    for task_id in os.listdir(exp_directory):
        with open(exp_directory + '/' + task_id + f'/{exp_name}.jsonl', 'r') as f:
            exp_lines = f.readlines()

        for line in exp_lines:
            line = json.loads(line)
            if line['issue_description'].strip() == issue_description.strip():
                continue
            exps = line['exps']
            success_exps = filter_successful_experiences(line['issue_description'], exps)
            total_exps.extend(success_exps)
    print("success_exps =", len(total_exps))
    final_dir = Path('/'.join(task_dir.split('/')[:-2]), f'{repo_name}_{exp_name}.jsonl')
    with open(final_dir, 'w') as w:
        for cur_exps in total_exps:
            w.write(json.dumps(cur_exps) + "\n")

    return final_dir


def extract_json_from_string(input_str):
    """
    Extract JSON data wrapped with triple backticks (```json ... ```).

    Parameters:
        input_str (str): The input string containing JSON data wrapped with triple backticks.

    Returns:
        dict: Parsed JSON data if valid JSON is found, otherwise None.
    """
    # Use regex to find JSON content wrapped with ```json
    match = re.search(r"```json\s*(\{.*?\})\s*```", input_str, re.DOTALL)

    if match:
        json_str = match.group(1)
        try:
            # Parse the extracted JSON string into a dictionary
            return json.loads(json_str)
        except json.JSONDecodeError as ee:
            print(json_str)
            print(f"{ee}\n Invalid JSON format.")
            return None
    else:
        print("No JSON data found wrapped with triple backticks.")
        return None


def load_json_exps(exp_dir):
    if not os.path.exists(exp_dir):
        return None

    with open(exp_dir, 'r') as r:
        exps_json = json.load(r)

    return exps_json


def convert_exp_json_to_str(exps_json):
    tester_exps = exps_json.get('tester_exps', [])
    coder_exps = exps_json.get('coder_exps', [])

    tester_exps_str = ""
    for t_idx, t_exp in enumerate(tester_exps):
        if t_idx + 1 > 15:
            break
        tester_exps_str += f"{t_idx + 1}. " + t_exp.strip() + '\n'

    coder_exps_str = ""
    for c_idx, c_exp in enumerate(coder_exps):
        if c_idx + 1 > 15:
            break
        coder_exps_str += f"{c_idx + 1}. " + c_exp.strip() + '\n'

    return tester_exps_str.strip(), coder_exps_str.strip()


def ase_extract_result(text):
    """
    Extracts content from <test_analysis>, <test_correct>, and <test_advice> tags
    and returns a dict in the desired format.
    """
    result = {
        "test-analysis": "",
        "if-reproduce": "",
        "test-advice": ""
    }

    # Define regex patterns for each tag (non-greedy, DOTALL for multiline)
    patterns = {
        "test-analysis": r"<test_analysis>(.*?)</test_analysis>",
        "if-reproduce": r"<test_correct>(.*?)</test_correct>",
        "test-advice": r"<test_advice>(.*?)</test_advice>"
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if not match:
            return None  # If any tag is missing/incomplete, return None
        result[key] = match.group(1).strip()

    return result


def ase_remove_summary_block(text):
    pattern = r'Summary:\nNumber of test cases confirming the issue exists: \d+\nTotal number of test cases: \d+\n?'
    return re.sub(pattern, '', text)

if __name__ == '__main__':
    pass
