import json
import os
import re
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import List, Dict
from typing import TypeAlias

import numpy as np
from loguru import logger
from rank_bm25 import BM25Okapi

from agents import agent_common
from agents.agent_common import InvalidLLMResponse
from data_structures import BugLocation, MessageThread
from log import print_acr, print_patch_generation
from model import common
from post_process import (
    ExtractStatus,
    convert_response_to_diff,
    record_extract_status, record_extract_status_idx,
)
from search.search_manage import SearchManager
from task import Task

SYSTEM_PROMPT_W_REPO = """You are a software developer maintaining the GitHub project {repo_name}.
You are working on an issue submitted to your project.
The issue contains a description marked between <issue> and </issue>.
Another developer has already collected code context related to the issue for you.
Your task is to write a patch that resolves this issue.
Do not make changes to test files or write tests; you are only interested in crafting a patch."""


WRITE_PATCH_PROMPT = """
### Phase 1: FIX ANALYSIS
1. Review the issue description and state clearly what the problem is.
2. Review the test script and its execution results, and state clearly how the test reproduces the issue.
3. Analyze the provided code context and specify where the problem occurs in the code.
4. State clearly the best practices to take into account in the fix.
5. State clearly how to fix the problem.

### Phase 2: FIX IMPLEMENTATION
1. Focus on making minimal, precise, and relevant changes to resolve the issue.
2. Include any necessary imports introduced by the patch.
3. Write the patch using the strict format specified below:
- Each modification must be enclosed in:
  - `<file>...</file>`: replace `...` with actual file path.
  - `<original>...</original>`: replace `...` with the original code snippet from the provided code locations.
  - `<patched>...</patched>`: replace `...` with the fixed version of the original code.
- The `<original>` block must contain an exact, continuous block of code from the provided code locations, as the system relies on this for locating modifications.
- When adding original code and patched code, pay attention to indentation, as the code is in Python.
- DO NOT include line numbers in the patch.
- You can write up to three modifications if needed.

EXAMPLE PATCH FORMAT:
# modification 1
```
<file>...</file>
<original>...</original>
<patched>...</patched>
```

# modification 2
```
<file>...</file>
<original>...</original>
<patched>...</patched>
```

# modification 3
```
...
```
"""


WRITE_PATCH_PROMPT_WO_TEST = """
### Phase 1: FIX ANALYSIS
1. Review the issue description and state clearly what the problem is.
2. Analyze the provided code context and specify where the problem occurs in the code.
3. State clearly the best practices to take into account in the fix.
4. State clearly how to fix the problem.

### Phase 2: FIX IMPLEMENTATION
1. Focus on making minimal, precise, and relevant changes to resolve the issue.
2. Include any necessary imports introduced by the patch.
3. Write the patch using the strict format specified below:
- Each modification must be enclosed in:
  - `<file>...</file>`: replace `...` with actual file path.
  - `<original>...</original>`: replace `...` with the original code snippet from the provided code locations.
  - `<patched>...</patched>`: replace `...` with the fixed version of the original code.
- The `<original>` block must contain an exact, continuous block of code from the provided code locations, as the system relies on this for locating modifications.
- When adding original code and patched code, pay attention to indentation, as the code is in Python.
- DO NOT include line numbers in the patch.
- You can write up to three modifications if needed.

EXAMPLE PATCH FORMAT:
# modification 1
```
<file>...</file>
<original>...</original>
<patched>...</patched>
```

# modification 2
```
<file>...</file>
<original>...</original>
<patched>...</patched>
```

# modification 3
```
...
```
"""

EXPAND_THINK_PROMPT = """However, the reproduction test is designed solely to reproduce the reported issue. Passing this test does not necessarily indicate that the issue has been fully resolved or that the patch is free from unintended side effects.
Your task is to carefully analyze the given candidate patch and provide detailed, actionable suggestions to improve its **comprehensiveness** in fully resolving the issue.

### Steps:
1. Review the issue description to thoroughly understand the core problem.
2. Identify any potential flaws or limitations in the candidate patch, including but not limited to:
   - Missed edge cases or scenarios the patch does not cover.
   - Missing complementary implementations (e.g., defining `__radd__` when adding `__add__`, or pairing `max()` with `min()` if appropriate).
   - Risks of regressions or unintended side effects in related functionalities.
   - Non-adherence to best practices (e.g., poor error handling, Inconsistent adherence to coding conventions).
3. Based on your analysis, propose clear and actionable improvement suggestions.
   - You can suggest either improvements to the existing patch or a new implementation in a different location if necessary.
   - Suggestions must aim to make a **meaningful, functional improvement** to the candidate patch.
   - Avoid superficial changes such as documentation edits, unrelated code refactoring, or restating existing patches.

Wrap your analysis process in <analysis> tags, and provide the suggestions in <advice> tags.
### Output Format:
<analysis>...</analysis>
<advice>...</advice>
"""


COMPRESS_THINK_PROMPT = """However, the reproduction test is designed solely to reproduce the reported issue. Passing this test does not necessarily indicate that the issue has been fully resolved or that the patch is free from unintended side effects.
Your task is to carefully analyze the given candidate patch and provide detailed, actionable suggestions to improve its **effectiveness and simplicity** in resolving the issue.

### Steps:
1. Review the issue description to thoroughly understand the core problem.
2. Identify any potential flaws or limitations in the candidate patch, including but not limited to:
   - Overly complex solutions, where certain modifications are unnecessary and can be removed without affecting correctness.
   - Indirect, convoluted implementations that could be replaced with cleaner, more direct alternatives.
   - Risks of regressions or unintended side effects in related functionalities.
   - Non-adherence to best practices (e.g., lack of simplicity, Inconsistent adherence to coding conventions).
3. Based on your analysis, propose clear and actionable improvement suggestions.
   - You can suggest either improvements to the existing patch or a new implementation in a different location if necessary.
   - Suggestions must aim to make a **meaningful, functional improvement** to the candidate patch.
   - Avoid superficial changes such as documentation edits, unrelated code refactoring, or restating existing patches.

Wrap your analysis process in <analysis> tags, and provide the suggestions in <advice> tags.
### Output Format:
<analysis>...</analysis>
<advice>...</advice>
"""


EXPAND_WRITE_PROMPT = """However, the reproduction test is designed solely to reproduce the reported issue. Passing this test does not necessarily indicate that the issue has been fully resolved or that the patch is free from unintended side effects.
Below are the analysis and improvement suggestions for the candidate patch provided by your colleague:
{patch_suggestions}

Your task is to propose a refined patch based on the provided analysis and improvement suggestions.
Steps:
1. Carefully review the provided analysis and suggestions to identify specific areas for improvement in the candidate patch.
2. Propose a refined patch that addresses the identified limitations. Clearly explain how each suggestion leads to the specific modifications you propose.

Output Format:
Explain your reasoning step by step, then write your patch using the following strict format:
- Each modification must be enclosed in:
  - `<file>...</file>`: replace `...` with actual file path.
  - `<original>...</original>`: replace `...` with the original code snippet from the provided code locations.
  - `<patched>...</patched>`: replace `...` with the fixed version of the original code.
- The `<original>` block must contain an exact, continuous block of code from the provided code locations, as the system relies on this for locating modifications.
- When adding original code and patched code, pay attention to indentation, as the code is in Python.
- DO NOT include line numbers in the patch.
- You can write UP TO THREE modifications if needed.
- Include any necessary imports required by your patch.

EXAMPLE PATCH FORMAT:
# modification 1
```
<file>...</file>
<original>...</original>
<patched>...</patched>
```

# modification 2
```
<file>...</file>
<original>...</original>
<patched>...</patched>
```

# modification 3
```
...
```
"""

PatchHandle: TypeAlias = str


class PatchAgent:
    EMPTY_PATCH_HANDLE = "EMPTY"

    def __init__(
        self,
        task: Task,
        search_manager: SearchManager,
        issue_stmt: str,
        context_thread: MessageThread,
        bug_locs: list[BugLocation],
        task_dir: str,
    ) -> None:
        self.task = task
        self.search_manager = search_manager
        self.issue_stmt = issue_stmt
        self.context_thread = context_thread  # the search conv historh thread
        # TODO: merge class_context_code into bug_loc_info, and make them one type
        self.bug_locs: list[BugLocation] = bug_locs
        self.task_dir = task_dir

        self._request_idx: int = -1
        self._responses: dict[PatchHandle, str] = {}
        self._diffs: dict[PatchHandle, str] = {}
        self._feedbacks: dict[PatchHandle, list[str]] = defaultdict(list)
        self._history: list[PatchHandle] = []


    def write_multiple_patch_wo_memory(
        self, test_content, orig_repro_result, retries: int = 3, patch_nums=4
    ):
        return self._write_multiple_patch_without_memory(
            test_content, orig_repro_result,
            max_feedbacks=0, retries=retries, patch_nums=patch_nums)

    def write_multiple_patch_wo_memory_wo_test(
        self, retries: int = 3, patch_nums=4
    ):
        return self._write_multiple_patch_without_memory_without_test(
            max_feedbacks=0, retries=retries, patch_nums=patch_nums)

    def add_feedback(self, handle: PatchHandle, feedback: str) -> None:
        if handle not in self._diffs:
            raise ValueError("patch {} does not exist", handle)

        self._feedbacks[handle].append(feedback)

    def _write_multiple_patch_without_memory(
        self, test_content, orig_repro_result,
            max_feedbacks: int, retries: int, patch_nums = 4
    ):
        max_feedbacks = max_feedbacks if max_feedbacks >= 0 else len(self._history)
        num_feedbacks = min(max_feedbacks, len(self._history))
        history_handles = self._history[-num_feedbacks:]

        for _ in range(retries):
            return_result, thread = self._call_api_write_multipatch_without_memory(
                test_content, orig_repro_result,
                patch_nums,
                history_handles
            )
            self._request_idx += 1

            # print_patch_generation(response)
            # Path(self.task_dir, f"patch_raw_{self._request_idx}.md").write_text(
            #     response
            # )
            thread.save_to_file(
                Path(self.task_dir, f"conv_patch_{self._request_idx}.json")
            )

            applicable_patch = []
            applicable_response = []
            for result_idx, (applicable, response, diff_content) in enumerate(return_result):
                print_patch_generation(response)
                Path(self.task_dir, f"patch_{result_idx}_raw_{self._request_idx}.md").write_text(
                    response
                )

                msg = f"Patch is applicable" if applicable else "Patch is not applicable"
                print_acr(msg)

                if applicable:
                    applicable_patch.append(diff_content)
                    applicable_response.append(response)

                    print_acr(f"```diff\n{diff_content}\n```", f"Extracted patch {result_idx}")

            if applicable_patch:
                # todo put it elsewhere
                # handle = self._register_applicable_multiple_patch(applicable_response, applicable_patch)

                return str(self._request_idx), applicable_patch, applicable_response

        raise InvalidLLMResponse(
            f"Failed to write an applicable patch in {retries} attempts"
        )

    def _write_multiple_patch_without_memory_without_test(
        self, max_feedbacks: int, retries: int, patch_nums = 4
    ):
        max_feedbacks = max_feedbacks if max_feedbacks >= 0 else len(self._history)
        num_feedbacks = min(max_feedbacks, len(self._history))
        history_handles = self._history[-num_feedbacks:]

        for _ in range(retries):
            return_result, thread = self._call_api_write_multipatch_without_memory_without_test(
                patch_nums,
                history_handles
            )
            self._request_idx += 1

            # print_patch_generation(response)
            # Path(self.task_dir, f"patch_raw_{self._request_idx}.md").write_text(
            #     response
            # )
            thread.save_to_file(
                Path(self.task_dir, f"conv_patch_{self._request_idx}.json")
            )

            applicable_patch = []
            applicable_response = []
            for result_idx, (applicable, response, diff_content) in enumerate(return_result):
                print_patch_generation(response)
                Path(self.task_dir, f"patch_{result_idx}_raw_{self._request_idx}.md").write_text(
                    response
                )

                msg = f"Patch is applicable" if applicable else "Patch is not applicable"
                print_acr(msg)

                if applicable:
                    applicable_patch.append(diff_content)
                    applicable_response.append(response)

                    print_acr(f"```diff\n{diff_content}\n```", f"Extracted patch {result_idx}")

            if applicable_patch:
                # todo put it elsewhere
                # handle = self._register_applicable_multiple_patch(applicable_response, applicable_patch)

                return str(self._request_idx), applicable_patch, applicable_response

        raise InvalidLLMResponse(
            f"Failed to write an applicable patch in {retries} attempts"
        )


    def _expand_patch(self, test_content, orig_repro, patch, patched_repro):
        for _ in range(3):
            thread_thinking = self._construct_init_thread()

            # test_prompt = (
            #     f"Here is the reproduction test script and its execution result on the original buggy program, before any patches were applied:\n"
            #     f"### Test:\n```python\n{test_content.strip()}\n```\n")
            # test_prompt += (f"### stdout:\n{orig_repro.stdout.strip()}\n" +
            #                 f"### stderr:\n{orig_repro.stderr.strip()}\n\n")

            # patch_prompt = f"Here is a candidate patch and the execution results from running the above reproduction script after applying it:\n"
            # patch_prompt += f"### Patch:\n```python\n{patch.strip()}\n```\n"
            # patch_prompt += (f"### stdout:\n{patched_repro.stdout.strip()}\n" +
            #                  f"### stderr:\n{patched_repro.stderr.strip()}\n\n")

            # thread.add_user(test_prompt.strip() + '\n\n' + patch_prompt.strip())

            patch_prompt = "This is a candidate patch provided by your colleague that has successfully passed an initial reproduction test:\n"
            patch_prompt += f"### Patch:\n```python\n{patch.strip()}\n```\n"
            # thread.add_user(patch_prompt)

            thread_thinking.add_user(patch_prompt + EXPAND_THINK_PROMPT)
            print_acr(patch_prompt + EXPAND_THINK_PROMPT)

            total_response, total_result = [], []
            for try_idx in range(5):
                response, *_ = common.SELECTED_MODEL.call(
                    thread_thinking.to_msg()
                )
                print('SELECTED_MODEL:\n', response)
                eval_result = ase_extract_suggestions(response)

                if not isinstance(eval_result, dict):
                    print("InvalidLLMResponse")
                    continue
                else:
                    total_response.append(response)
                    total_result.append(eval_result)
                    break

            for try_idx in range(5):
                response, *_ = common.GPTo4_MODEL.call(
                    thread_thinking.to_msg()
                )
                print('GPTo4_MODEL:\n', response)
                eval_result = ase_extract_suggestions(response)

                if not isinstance(eval_result, dict):
                    print("InvalidLLMResponse")
                    continue
                else:
                    total_response.append(response)
                    total_result.append(eval_result)
                    break

            for try_idx in range(5):
                response, *_ = common.GPTo4_MODEL.call(
                    thread_thinking.to_msg()
                )
                print('GPTo4_MODEL:\n', response)
                eval_result = ase_extract_suggestions(response)

                if not isinstance(eval_result, dict):
                    print("InvalidLLMResponse")
                    continue
                else:
                    total_response.append(response)
                    total_result.append(eval_result)
                    break

            total_patch = []
            total_patch_response = []

            thread_writing = self._construct_init_thread()
            for p_idx, advice_result in enumerate(total_result):
                suggestion_prompt = (f'### Analysis:\n{advice_result["analysis"].strip()}\n'
                                     f'### Suggestions:\n{advice_result["advice"].strip()}')

                thread_writing.add_user(patch_prompt + EXPAND_WRITE_PROMPT.format(patch_suggestions=suggestion_prompt))

                for try_idx in range(3):
                    response, *_ = common.SELECTED_MODEL.call(thread_writing.to_msg())
                    extract_status, _, diff_content = convert_response_to_diff(
                        response, self.task_dir
                    )
                    record_extract_status(self.task_dir, extract_status)

                    applicable = (extract_status == ExtractStatus.APPLICABLE_PATCH)

                    # applicable, response, diff_content, thread = self._write_multiple_patch_w_rule_v2(
                    #     summarized_rules, cur_temperature
                    # )
                    print_patch_generation(response)
                    msg = "Patch is applicable" if applicable else "Patch is not applicable"
                    print_acr(msg)
                    if applicable:
                        print_acr(f"```diff\n{diff_content}\n```", "Extracted patch")

                        Path(self.task_dir, f"expand_patch_raw_{p_idx}.md").write_text(
                            response
                        )
                        total_patch.append(diff_content)
                        total_patch_response.append(response)
                        break

            if total_patch:
                for think_response, patch_response in zip(total_response, total_patch_response):
                    thread_thinking.add_model(think_response)
                    thread_writing.add_model(patch_response)

                thread_thinking.save_to_file(
                    Path(self.task_dir, f"conv_expand_patch_thinking.json")
                )
                thread_writing.save_to_file(
                    Path(self.task_dir, f"conv_expand_patch_writing.json")
                )
                return total_patch, total_patch_response

        return None, None


    def _compress_patch(self, test_content, orig_repro, patch, patched_repro):
        for _ in range(3):
            thread_thinking = self._construct_init_thread()

            # test_prompt = (
            #     f"Here is the reproduction test script and its execution result on the original buggy program, before any patches were applied:\n"
            #     f"### Test:\n```python\n{test_content.strip()}\n```\n")
            # test_prompt += (f"### stdout:\n{orig_repro.stdout.strip()}\n" +
            #                 f"### stderr:\n{orig_repro.stderr.strip()}\n\n")

            # patch_prompt = f"Here is a candidate patch and the execution results from running the above reproduction script after applying it:\n"
            # patch_prompt += f"### Patch:\n```python\n{patch.strip()}\n```\n"
            # patch_prompt += (f"### stdout:\n{patched_repro.stdout.strip()}\n" +
            #                  f"### stderr:\n{patched_repro.stderr.strip()}\n\n")

            # thread.add_user(test_prompt.strip() + '\n\n' + patch_prompt.strip())

            patch_prompt = "This is a candidate patch provided by your colleague that has successfully passed an initial reproduction test:\n"
            patch_prompt += f"### Patch:\n```python\n{patch.strip()}\n```\n"
            # thread.add_user(patch_prompt)

            thread_thinking.add_user(patch_prompt + COMPRESS_THINK_PROMPT)
            print_acr(patch_prompt + COMPRESS_THINK_PROMPT)

            total_response, total_result = [], []
            for try_idx in range(5):
                response, *_ = common.SELECTED_MODEL.call(
                    thread_thinking.to_msg()
                )
                print('SELECTED_MODEL:\n', response)
                eval_result = ase_extract_suggestions(response)

                if not isinstance(eval_result, dict):
                    print("InvalidLLMResponse")
                    continue
                else:
                    total_response.append(response)
                    total_result.append(eval_result)
                    break

            for try_idx in range(5):
                response, *_ = common.GPTo4_MODEL.call(
                    thread_thinking.to_msg()
                )
                print('GPTo4_MODEL:\n', response)
                eval_result = ase_extract_suggestions(response)

                if not isinstance(eval_result, dict):
                    print("InvalidLLMResponse")
                    continue
                else:
                    total_response.append(response)
                    total_result.append(eval_result)
                    break

            for try_idx in range(5):
                response, *_ = common.GPTo4_MODEL.call(
                    thread_thinking.to_msg()
                )
                print('GPTo4_MODEL:\n', response)
                eval_result = ase_extract_suggestions(response)

                if not isinstance(eval_result, dict):
                    print("InvalidLLMResponse")
                    continue
                else:
                    total_response.append(response)
                    total_result.append(eval_result)
                    break

            total_patch = []
            total_patch_response = []

            thread_writing = self._construct_init_thread()
            for p_idx, advice_result in enumerate(total_result):
                suggestion_prompt = (f'### Analysis:\n{advice_result["analysis"].strip()}\n'
                                     f'### Suggestions:\n{advice_result["advice"].strip()}')

                thread_writing.add_user(patch_prompt + EXPAND_WRITE_PROMPT.format(patch_suggestions=suggestion_prompt))

                for try_idx in range(3):
                    response, *_ = common.SELECTED_MODEL.call(thread_writing.to_msg())
                    extract_status, _, diff_content = convert_response_to_diff(
                        response, self.task_dir
                    )
                    record_extract_status(self.task_dir, extract_status)

                    applicable = (extract_status == ExtractStatus.APPLICABLE_PATCH)

                    # applicable, response, diff_content, thread = self._write_multiple_patch_w_rule_v2(
                    #     summarized_rules, cur_temperature
                    # )
                    print_patch_generation(response)
                    msg = "Patch is applicable" if applicable else "Patch is not applicable"
                    print_acr(msg)
                    if applicable:
                        print_acr(f"```diff\n{diff_content}\n```", "Extracted patch")

                        Path(self.task_dir, f"expand_patch_raw_{p_idx}.md").write_text(
                            response
                        )
                        total_patch.append(diff_content)
                        total_patch_response.append(response)
                        break

            if total_patch:
                for think_response, patch_response in zip(total_response, total_patch_response):
                    thread_thinking.add_model(think_response)
                    thread_writing.add_model(patch_response)

                thread_thinking.save_to_file(
                    Path(self.task_dir, f"conv_expand_patch_thinking.json")
                )
                thread_writing.save_to_file(
                    Path(self.task_dir, f"conv_expand_patch_writing.json")
                )
                return total_patch, total_patch_response

        return None, None


    def _refine_patch(
            self, test, orig_repro, patch, patched_repro, round_idx, g_idx, eval_result
    ):
        for _ in range(3):
            thread = self._construct_init_thread()

            test_prompt = (
                f"Here is the reproduction test script and its execution result on the original buggy program, before any patches were applied:\n"
                f"### Test:\n```python\n{test.strip()}\n```\n")
            test_prompt += (f"### stdout:\n{orig_repro.stdout.strip()}\n" +
                            f"### stderr:\n{orig_repro.stderr.strip()}\n\n")

            # prefix_thread.add_user(test_prompt)

            patch_prompt = f"Here is the patch you have written and the execution results from running the above test script after applying it:\n"
            patch_prompt += f"### Patch:\n```python\n{patch.strip()}\n```\n"
            patch_prompt += (f"### stdout:\n{patched_repro.stdout.strip()}\n" +
                             f"### stderr:\n{patched_repro.stderr.strip()}\n\n")

            thread.add_user(test_prompt + patch_prompt.strip())

            eval_prompt = ('Your colleague believes this patch does not fully resolve the above issue. '
                           f'Below is his analysis and suggestions:\n'
                           f'### Analysis:\n{eval_result["patch_analysis"].strip()}\n'
                           f'### Suggestions:\n{eval_result["patch_advice"].strip()}')

            refine_prompt = eval_prompt.strip() + '\n###\n\n' + """Your task is to propose a new, correct patch that fully resolves the issue based on the provided analysis and improvement suggestions.

Steps:
1. Carefully review the provided analysis and suggestions to identify specific areas for improvement in the candidate patch.
2. Propose a new, correct patch to resolve the issue. Clearly explain how each suggestion leads to the specific modifications you propose.

Output Format:
Explain your reasoning step by step, then write your patch using the following strict format:
- Each modification must be enclosed in:
   - `<file>...</file>`: replace `...` with actual file path.
   - `<original>...</original>`: replace `...` with the original code snippet from the provided code locations.
   - `<patched>...</patched>`: replace `...` with the fixed version of the original code.
- The `<original>` block must contain an exact, continuous block of code from the provided code locations, as the system relies on this for locating modifications.
- When adding original code and patched code, pay attention to indentation, as the code is in Python.
- DO NOT include line numbers in the patch.
- You can write UP TO THREE modifications if needed.
- Include any necessary imports required by your patch.

EXAMPLE PATCH FORMAT:
# modification 1
```
<file>...</file>
<original>...</original>
<patched>...</patched>
```

# modification 2
```
<file>...</file>
<original>...</original>
<patched>...</patched>
```

# modification 3
```
...
```
"""

            thread.add_user(refine_prompt)
            print_acr(refine_prompt)

            patch_resp, *_ = common.SELECTED_MODEL.call(thread.to_msg())
            thread.add_model(patch_resp)

            # new_patches = extract_new_patches(new_patch_answer)
            # if len(new_patches) != 1:
            #     continue
            # else:
            #     new_patch = new_patches[0]
            #
            # format_thread = self._construct_init_thread_format()
            # format_thread.add_user("Here is the code patch in unified diff format:\n" + new_patch.strip())
            # format_thread.add_user(MODIFICATION_FORMAT_PROMPT)
            #
            # patch_resp, *_ = common.SELECTED_MODEL.call(format_thread.to_msg())
            # format_thread.add_model(patch_resp)

            extract_status, _, diff_content = convert_response_to_diff(
                patch_resp, self.task_dir
            )
            record_extract_status(self.task_dir, extract_status)

            applicable = (extract_status == ExtractStatus.APPLICABLE_PATCH)
            response = patch_resp

            # applicable, response, diff_content, thread = self._write_multiple_patch_w_rule_v2(
            #     summarized_rules, cur_temperature
            # )
            print_patch_generation(response)
            msg = "Patch is applicable" if applicable else "Patch is not applicable"
            print_acr(msg)
            if applicable:
                print_acr(f"```diff\n{diff_content}\n```", "Extracted patch")

                Path(self.task_dir, f"refine_patch_raw_{round_idx}_{g_idx}.md").write_text(
                    response
                )
                thread.save_to_file(
                    Path(self.task_dir, f"conv_refine_patch_{round_idx}_{g_idx}.json")
                )
                # format_thread.save_to_file(
                #     Path(self.task_dir, f"conv_refine_format_patch_{round_idx}_{g_idx}.json")
                # )
                return diff_content, response

        return None, None


    def _refine_patch_W_EXP(
            self, test, orig_repro, patch, patched_repro, round_idx, g_idx, eval_result
    ):
        for _ in range(3):
            thread = self._construct_init_thread()

            ########## todo retrieve experiences and add them to feedback
            retrieved_exps, sim_scores = get_experiences_patch(
                self.task.get_issue_statement(),
                patch,
                self.task.repo_name.split('/')[-1],
                self.task_dir,
                exp_name='patch_experiences'
            )

            example_prompt = "Here are examples of your colleague refining incorrect patches into correct ones for other issues:\n"
            for idx, cur_exp in enumerate(retrieved_exps[:1]):
                example_prompt += f"=== Example {idx + 1} ===\n"
                example_prompt += ("### Wrong Patch:\n```python\n" + cur_exp['old_patch'].strip() + '\n```\n\n' +
                                   '### Correct Patch:\n```python\n' + cur_exp['new_patch'].strip() + '\n```')
                example_prompt += "\n\n"
            example_prompt = example_prompt.strip()

            test_prompt = (
                f"Here is the reproduction test script and its execution result on the original buggy program, before any patches were applied:\n"
                f"### Test:\n```python\n{test.strip()}\n```\n")
            test_prompt += (f"### stdout:\n{orig_repro.stdout.strip()}\n" +
                            f"### stderr:\n{orig_repro.stderr.strip()}\n\n")

            # prefix_thread.add_user(test_prompt)

            patch_prompt = f"Here is the patch you have written and the execution results from running the above test script after applying it:\n"
            patch_prompt += f"### Patch:\n```python\n{patch.strip()}\n```\n"
            patch_prompt += (f"### stdout:\n{patched_repro.stdout.strip()}\n" +
                             f"### stderr:\n{patched_repro.stderr.strip()}\n\n")

            thread.add_user(test_prompt + patch_prompt.strip())

            eval_prompt = ('Your colleague believes this patch does not fully resolve the above issue. '
                           f'Below is his analysis and suggestions:\n'
                           f'### Analysis:\n{eval_result["patch_analysis"].strip()}\n'
                           f'### Suggestions:\n{eval_result["patch_advice"].strip()}')

            refine_prompt = eval_prompt.strip() + '\n###\n\n' + f"""Your task is to propose a new, correct patch that fully resolves the issue based on the provided analysis and improvement suggestions.

Steps:
1. Carefully review the provided analysis and suggestions to identify specific areas for improvement in the candidate patch.
2. Propose a new, correct patch to resolve the issue. Clearly explain how each suggestion leads to the specific modifications you propose.

{example_prompt}

Output Format:
Explain your reasoning step by step, then write your refined patch using the following strict format:
- Each modification must be enclosed in:
   - `<file>...</file>`: replace `...` with actual file path.
   - `<original>...</original>`: replace `...` with the original code snippet from the provided code locations.
   - `<patched>...</patched>`: replace `...` with the fixed version of the original code.
- The `<original>` block must contain an exact, continuous block of code from the provided code locations, as the system relies on this for locating modifications.
- When adding original code and patched code, pay attention to indentation, as the code is in Python.
- DO NOT include line numbers in the patch.
- You can write UP TO THREE modifications if needed.
- Include any necessary imports required by your patch.

EXAMPLE PATCH FORMAT:
# modification 1
```
<file>...</file>
<original>...</original>
<patched>...</patched>
```

# modification 2
```
<file>...</file>
<original>...</original>
<patched>...</patched>
```

# modification 3
```
...
```
"""

            thread.add_user(refine_prompt)
            print_acr(refine_prompt)

            patch_resp, *_ = common.SELECTED_MODEL.call(thread.to_msg())
            thread.add_model(patch_resp)

            # new_patches = extract_new_patches(new_patch_answer)
            # if len(new_patches) != 1:
            #     continue
            # else:
            #     new_patch = new_patches[0]
            #
            # format_thread = self._construct_init_thread_format()
            # format_thread.add_user("Here is the code patch in unified diff format:\n" + new_patch.strip())
            # format_thread.add_user(MODIFICATION_FORMAT_PROMPT)
            #
            # patch_resp, *_ = common.SELECTED_MODEL.call(format_thread.to_msg())
            # format_thread.add_model(patch_resp)

            extract_status, _, diff_content = convert_response_to_diff(
                patch_resp, self.task_dir
            )
            record_extract_status(self.task_dir, extract_status)

            applicable = (extract_status == ExtractStatus.APPLICABLE_PATCH)
            response = patch_resp

            # applicable, response, diff_content, thread = self._write_multiple_patch_w_rule_v2(
            #     summarized_rules, cur_temperature
            # )
            print_patch_generation(response)
            msg = "Patch is applicable" if applicable else "Patch is not applicable"
            print_acr(msg)
            if applicable:
                print_acr(f"```diff\n{diff_content}\n```", "Extracted patch")

                Path(self.task_dir, f"refine_patch_raw_{round_idx}_{g_idx}.md").write_text(
                    response
                )
                thread.save_to_file(
                    Path(self.task_dir, f"conv_refine_patch_{round_idx}_{g_idx}.json")
                )
                # format_thread.save_to_file(
                #     Path(self.task_dir, f"conv_refine_format_patch_{round_idx}_{g_idx}.json")
                # )
                return diff_content, response

        return None, None


    def _call_api_write_multipatch_without_memory(
        self,
        test_content, orig_repro_result,
        patch_nums=4,
        history_handles: list[PatchHandle] | None = None,
    ):
        history_handles = history_handles or []

        thread = self._construct_init_thread_w_reproduction(test_content, orig_repro_result)

        is_first_try = not any(handle in self._feedbacks for handle in history_handles)

        logger.debug(f"<agent write patch> is_first_try: {is_first_try}")

        prefix_prompt = "Your task is to analyze and resolve the given GitHub issue in two phases:"

        for handle in history_handles:
            feedbacks = self._feedbacks.get(handle, [])
            if not feedbacks:
                logger.warning("patch {} does not have a feedback; skipping", handle)
                continue

            thread.add_model(self._responses[handle], [])
            prefix_prompt = "Review the generated patch and its feedback. Then, incorporating the suggestions, propose a refined patch to resolve the given issue."

            for feedback in feedbacks:
                thread.add_user(feedback)

        # if not summarized_rules:
        thread.add_user(prefix_prompt + WRITE_PATCH_PROMPT)
        print_acr(prefix_prompt + WRITE_PATCH_PROMPT)

        # t1_nums = patch_nums // 2
        t_list = [0.0] + [0.8] * (patch_nums - 1)
        patch_resp_list = []
        for cur_temperature in t_list:
            patch_resp, *_ = common.SELECTED_MODEL.call(thread.to_msg(), temperature=cur_temperature)
            patch_resp_list.append(patch_resp)

        return_result = []
        for patch_idx, patch_resp in enumerate(patch_resp_list):
            thread.add_model(patch_resp)
            extract_status, _, diff_content = convert_response_to_diff(
                patch_resp, self.task_dir
            )
            record_extract_status_idx(self.task_dir, extract_status, patch_idx)
            return_result.append((extract_status == ExtractStatus.APPLICABLE_PATCH, patch_resp, diff_content))

        return (
            return_result,
            thread
        )

    def _call_api_write_multipatch_without_memory_without_test(
            self,
            patch_nums=6,
            history_handles: list[PatchHandle] | None = None,
    ):
        history_handles = history_handles or []

        thread = self._construct_init_thread()

        is_first_try = not any(handle in self._feedbacks for handle in history_handles)

        logger.debug(f"<agent write patch> is_first_try: {is_first_try}")

        prefix_prompt = "Your task is to analyze and resolve the given GitHub issue in two phases:"

        for handle in history_handles:
            feedbacks = self._feedbacks.get(handle, [])
            if not feedbacks:
                logger.warning("patch {} does not have a feedback; skipping", handle)
                continue

            thread.add_model(self._responses[handle], [])
            prefix_prompt = "Review the generated patch and its feedback. Then, incorporating the suggestions, propose a refined patch to resolve the given issue."

            for feedback in feedbacks:
                thread.add_user(feedback)

        # if not summarized_rules:
        thread.add_user(prefix_prompt + WRITE_PATCH_PROMPT_WO_TEST)
        print_acr(prefix_prompt + WRITE_PATCH_PROMPT_WO_TEST)

        # t1_nums = patch_nums // 2
        t_list = [0.0] + [0.8] * (patch_nums - 1)
        patch_resp_list = []
        for cur_temperature in t_list:
            patch_resp, *_ = common.SELECTED_MODEL.call(thread.to_msg(), temperature=cur_temperature)
            patch_resp_list.append(patch_resp)

        return_result = []
        for patch_idx, patch_resp in enumerate(patch_resp_list):
            thread.add_model(patch_resp)
            extract_status, _, diff_content = convert_response_to_diff(
                patch_resp, self.task_dir
            )
            record_extract_status_idx(self.task_dir, extract_status, patch_idx)
            return_result.append((extract_status == ExtractStatus.APPLICABLE_PATCH, patch_resp, diff_content))

        return (
            return_result,
            thread
        )



    def _construct_init_thread(self) -> MessageThread:
        """
        Construct the initial patch gen conv thread, based on whether bug location is available.
        """
        if self.bug_locs:
            # bug location is available
            thread = MessageThread()
            thread.add_system(SYSTEM_PROMPT_W_REPO.format(repo_name=self.task.repo_name))
            thread.add_user("<issue>\n" + self.issue_stmt.strip() + "\n</issue>")

            thread.add_user(self._construct_code_context_prompt_v2())
        else:
            assert 1 == 2

        return thread


    def _construct_init_thread_w_reproduction(
            self, test_content, orig_repro_result
    ) -> MessageThread:
        """
        Construct the initial patch gen conv thread, based on whether bug location is available.
        """
        if self.bug_locs:
            # bug location is available
            thread = MessageThread()
            thread.add_system(SYSTEM_PROMPT_W_REPO.format(repo_name=self.task.repo_name))
            thread.add_user("<issue>\n" + self.issue_stmt.strip() + "\n</issue>")

            reproduction_prompt = "Below is the reproduction script written by your colleague, along with its execution results on the original buggy program:\n"
            reproduction_prompt += f'```python\n{test_content}\n```\n'
            reproduction_prompt += f'Execution Results:\nSTDOUT:\n{orig_repro_result.stdout.strip()}\nSTDERR:\n{orig_repro_result.stderr.strip()}'
            thread.add_user(reproduction_prompt)

            thread.add_user(self._construct_code_context_prompt_v2())
        else:
            assert 1 == 2

        return thread


    def _construct_code_context_prompt_v2(self) -> str:
        prompt = "Here are the possible buggy locations from the original program collected by someone else.\n"

        prompt += BugLocation.multiple_locs_to_str_for_model_wo_intention(self.bug_locs)
        prompt += (
            "Note that you DO NOT NEED to modify every location; you should think what changes "
            "are necessary for resolving the issue, and only propose those modifications."
        )
        return prompt


def extract_json_from_string(input_str):
    import re, json
    """
    Extract JSON data wrapped with triple backticks (```json ... ```).

    Parameters:
        input_str (str): The input string containing JSON data wrapped with triple backticks.

    Returns:
        dict: Parsed JSON data if valid JSON is found, otherwise None.
    """
    # Use regex to find JSON content wrapped with ```json
    match = re.search(r"```json\s*(\[.*?\])\s*```", input_str, re.DOTALL)

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


def extract_patches(input_str):
    """
    Extract all patches (### Patch X followed by a code block) from the given string.

    Parameters:
        input_str (str): The input string containing patches.

    Returns:
        dict: A dictionary mapping patch numbers to their corresponding content.
    """
    patches = []

    # Regex pattern to capture "### Patch X:" followed by a code block
    pattern = re.findall(r"### Patch (.+?):\s*```(.*?)```", input_str, re.DOTALL)

    for patch_num, content in pattern:
        patches.append(content.strip())

    return patches


def extract_new_patches(text):
    """
    Extracts code snippets wrapped in <new_patch>...</new_patch> tags from the input text.

    Args:
        text (str): The text containing one or more <new_patch> sections.

    Returns:
        List[str]: A list of extracted patch strings.
    """
    pattern = r"<new_patch>(.*?)</new_patch>"
    patches = re.findall(pattern, text, re.DOTALL)
    return patches


def ase_extract_suggestions(text):
    """
    Extracts content from <patch_analysis>, <patch_correct>, and <patch_advice> tags
    and returns a dict in the desired format.
    """
    if '<analysis>' in text and '</analysis>' in text and '<advice>' in text and '</advice>' not in text:
        text = text + '</advice>'

    result = {
        "analysis": "",
        "advice": ""
    }

    # Define regex patterns for each tag (non-greedy, DOTALL for multiline)
    patterns = {
        "analysis": r"<analysis>(.*?)</analysis>",
        "advice": r"<advice>(.*?)</advice>",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if not match:
            return None  # If any tag is missing/incomplete, return None
        result[key] = match.group(1).strip()

    return result

def get_experiences_patch(issue_description, patch_content, repo_name, task_dir, exp_name):
    # Load and preprocess the knowledge base
    knowledge_base_file = save_experiences_patch(repo_name, task_dir, issue_description, exp_name)

    filter_kb = load_knowledge_base_patch(knowledge_base_file)
    print("=========================== num of available experiences", len(filter_kb), "==============================")

    if len(filter_kb) == 0:
        return [], 0

    # Build BM25 indices for specific fields
    bm25_indices = build_bm25_index_patch(filter_kb)

    weights = {
        "issue_description": 0.7,
        "old_patch": 0.3
    }

    query = {
        "issue_description": preprocess_text(issue_description),
        "old_patch": preprocess_text(patch_content),
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



def save_experiences_patch(repo_name, task_dir, issue_description, exp_name):
    exp_directory = '/'.join(task_dir.split('/')[:-1])
    print('patch_exp_dir:', exp_directory)
    total_exps = []
    for task_id in os.listdir(exp_directory):
        if not os.path.isfile(exp_directory + '/' + task_id + f'/{exp_name}.jsonl'):
            continue
        with open(exp_directory + '/' + task_id + f'/{exp_name}.jsonl', 'r') as f:
            exp_lines = f.readlines()

        for line in exp_lines:
            line = json.loads(line)
            if line['issue_description'].strip() == issue_description.strip():
                continue
            exps = line['exps']
            success_exps = filter_patch_experiences(line['issue_description'], exps)
            total_exps.extend(success_exps)
    print("success_exps =", len(total_exps))
    final_dir = Path('/'.join(task_dir.split('/')[:-2]), f'{repo_name}_{exp_name}.jsonl')
    with open(final_dir, 'w') as w:
        for cur_exps in total_exps:
            w.write(json.dumps(cur_exps) + "\n")

    return final_dir


def load_knowledge_base_patch(file_path: str):
    """Load knowledge base from JSONL file."""
    filter_kb = []
    with open(file_path, 'r') as f:
        for line in f:
            example = json.loads(line)

            if example['old_patch'] != "" and example['new_result'] is True:
                    filter_kb.append(example)

    return filter_kb


def build_bm25_index_patch(kb: List[Dict]):
    """Build BM25 index for a specific field from preprocessed JSONL knowledge base."""
    # feedback prompt consider issue_description and patch_content
    corpus = [preprocess_text(example['issue_description']) for example in kb]
    patch_corpus = [preprocess_text(example['old_patch']) for example in kb]
    return {
        'issue_description': BM25Okapi(corpus),
        'old_patch': BM25Okapi(patch_corpus)
    }



def retrieve_examples_with_weights(
    preprocessed_queries,
    bm25_indices,
    kb,
    weights,
    top_k,
):
    # Initialize a list to store combined scores for all examples
    combined_scores = [0.0] * len(kb)

    # Compute weighted scores for each field
    for field, weight in weights.items():
        if field in preprocessed_queries and field in bm25_indices:
            field_scores = bm25_indices[field].get_scores(preprocessed_queries[field])
            score_norm = (field_scores - np.min(field_scores)) / (np.max(field_scores) - np.min(field_scores) + 1e-8)

            combined_scores = [cs + weight * fs for cs, fs in zip(combined_scores, score_norm)]
    # Get the top-k examples based on combined scores
    top_indices = sorted(range(len(combined_scores)), key=lambda i: combined_scores[i], reverse=True)[:top_k]

    topk_exps = [kb[i] for i in top_indices]
    topk_scores = [combined_scores[i] for i in top_indices]
    return topk_exps, topk_scores



def filter_patch_experiences(issue_description, experiences):
    """
    Filters successful experiences by considering each entry's `new_returncode` and the following entries in sequence.

    Parameters:
        experiences (list): A list of experience dictionaries.

    Returns:
        list: A list of successful experiences.
    """
    successful_experiences = []

    for i in range(len(experiences)):
        if experiences[i]["old_patch"] != "":
            # Check if any subsequent entry has new_returncode == 0
            for j in range(i, len(experiences)):  # Start from the current entry
                new_result = experiences[i].get("new_result")
                if new_result is True:
                    successful_experiences.append({
                        "issue_description": issue_description,
                        "old_patch": experiences[i]["old_patch"],
                        "old_result": experiences[i]["old_result"],
                        "new_patch": experiences[j]["new_patch"],
                        "new_result": experiences[j]["new_result"],
                    })
                    break  # Stop searching once a match is found for the current entry

    return successful_experiences


def preprocess_text(text: str) -> List[str]:
    """Tokenize and preprocess text (lowercase, remove punctuation)."""
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)  # Remove punctuation
    text_split = text.split()
    text = ' '.join(text_split)
    return text.split()