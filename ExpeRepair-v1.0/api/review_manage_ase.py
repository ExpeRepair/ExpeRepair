import json
import os
from datetime import datetime
from pathlib import Path

from loguru import logger

from agents import agent_reviewer
from agents.agent_common import InvalidLLMResponse
from agents.agent_reproducer import TestAgent, TestHandle
from agents.agent_write_patch import PatchAgent, PatchHandle
from data_structures import BugLocation, MessageThread, ReproResult
from search.search_manage import SearchManager
from task import SweTask, Task


class ReviewManager:
    def __init__(
        self,
        context_thread: MessageThread,
        bug_locs: list[BugLocation],
        search_manager: SearchManager,
        task: Task,
        output_dir: str,
        test_agent: TestAgent,
        repro_result_map: (
            dict[tuple[PatchHandle, TestHandle], ReproResult] | None
        ) = None,
        use_exps = False,
        sum_exps = 0
    ) -> None:
        self.issue_stmt = task.get_issue_statement()
        self.patch_agent = PatchAgent(
            task,
            search_manager,
            self.issue_stmt,
            context_thread,
            bug_locs,
            output_dir
        )
        # self.test_agent = TestAgent(task, output_dir)
        self.test_agent = test_agent
        self.task: Task = task
        self.repro_result_map: dict[tuple[PatchHandle, TestHandle], ReproResult] = dict(
            repro_result_map or {}
        )
        self.output_dir = output_dir
        self.use_exps = use_exps
        self.sum_exps = sum_exps


    def generator(
        self, rounds: int = 3, patch_content_list = []
    ):
        """
        This is the generator when reproducer is available.
        """
        assert isinstance(
            self.task, SweTask
        ), "Only SweTask is supported for reproducer+patch generator."

        try:
            yield from self._generator(rounds, patch_content_list)
        except InvalidLLMResponse as e:
            logger.info("Aborting review with exception: {}", str(e))

    def _generator(
            self, rounds: int, patch_content_list
    ):
        issue_statement = self.task.get_issue_statement()

        # TODO double check by running again
        if not self.test_agent._history:
            (
                test_handle,
                test_content,
                orig_repro_result,
            ) = self.test_agent.write_reproducing_test_W_EXP()
            # TODO NO verification test
            if not test_content or not orig_repro_result:
                print("===============================================================")
                print("No verification test, write multiple patches")
                print("===============================================================")
                # TODO expand tests
                test_inputs_list = self.test_agent._write_test_inputs_wo_reproduction(
                    test_nums=3
                )

                non_expand_patch_list, expand_patch_list = [], []

                # write the first patch
                (
                    patch_handle,
                    patch_content_list,
                    patch_response_list
                ) = self.patch_agent.write_multiple_patch_wo_memory_wo_test(
                    patch_nums=4
                )

                non_expand_patch_list.extend(patch_content_list)
                patch_content_list = list(set(patch_content_list))

                # TODO expand patches
                new_patch_content_list, new_patch_response_list = [], []
                for temp_patch in patch_content_list:
                    new_patch_list, new_response_list = self.patch_agent._expand_patch(
                        '', '', temp_patch, ''
                    )
                    new_patch_content_list.extend(new_patch_list)

                    new_patch_list, new_response_list = self.patch_agent._compress_patch(
                        '', '', temp_patch, ''
                    )
                    new_patch_content_list.extend(new_patch_list)

                global_patch_idx = 0
                expand_patch_list.extend(new_patch_content_list)
                for temp_patch in non_expand_patch_list:
                    self.save_patch(str(global_patch_idx), temp_patch)
                    global_patch_idx += 1
                for temp_patch in expand_patch_list:
                    self.save_patch_expand(global_patch_idx, temp_patch)
                    global_patch_idx += 1

                all_patch_list = non_expand_patch_list + expand_patch_list
                print("===============================================================")
                print(f"patch num: {len(all_patch_list)}")
                print("===============================================================")

                with open(Path(self.output_dir,
                               f"result_{len(all_patch_list)}_patch_{len(test_inputs_list)}_test.jsonl"),
                          'w') as w:
                    for temp_patch in all_patch_list:
                        patch_dict = {'patch_content': temp_patch,
                                      'repro_stdout': '', 'repro_stderr': '',
                                      'differential_test': []}
                        for test_input_content in test_inputs_list:
                            valid_repro_result = self.task.execute_test(
                                test_input_content, temp_patch
                            )
                            patch_dict['differential_test'].append(
                                {'test': test_input_content, 'stdout': valid_repro_result.stdout, 'stderr': valid_repro_result.stderr}
                            )
                        json.dump(patch_dict, w)
                        w.write('\n')

                yield "0", all_patch_list

            self.test_agent.save_test(test_handle)
        else:
            test_handle = self.test_agent._history[-1]
            test_content = self.test_agent._tests[test_handle]
            orig_repro_result = self.repro_result_map[
                (PatchAgent.EMPTY_PATCH_HANDLE, test_handle)
            ]

        coords = (PatchAgent.EMPTY_PATCH_HANDLE, test_handle)
        self.repro_result_map[coords] = orig_repro_result
        if orig_repro_result is not None:
            self.save_execution_result(orig_repro_result, *coords)

        # TODO expand tests
        test_inputs_list = self.test_agent._write_test_inputs_w_reproduction(
            test_content, orig_repro_result, test_nums=3
        )
        # # TODO for test
        # test_inputs_list = [test_content]

        if not patch_content_list:
            # write the first patch
            (
                patch_handle,
                patch_content_list,
                patch_response_list
            ) = self.patch_agent.write_multiple_patch_wo_memory(
                test_content, orig_repro_result, patch_nums=4
            )
            # self.save_patch(patch_handle, patch_content)
        print('patch num in the initial stage', len(patch_content_list))

        experiences = []
        exp_path = Path(self.output_dir, f"patch_experiences.jsonl")
        old_generated_patch = ''

        all_generated_patch_repro = []
        global_patch_idx = 0
        for round_idx_ in range(rounds + 1):
            # todo first select, then review, finally refine
            patched_repro_list = []
            for temp_patch in patch_content_list:
                # TODO save in the end
                self.save_patch(str(global_patch_idx), temp_patch)
                global_patch_idx += 1

                temp_patched_repro = self.task.execute_test(
                    test_content, temp_patch
                )

                patched_repro_list.append(temp_patched_repro)

                # if pass_the_test(orig_repro_result, temp_patched_repro):
                #     passed_patch_repro.append((temp_patch, temp_patched_repro))
                # else:
                #     unpassed_patch_repro.append((temp_patch, temp_patched_repro))

            rank_list, correct_list, review_thread = agent_reviewer.review_multiple_patch_select_best(
                self.task.repo_name,
                issue_statement,
                self.patch_agent.bug_locs,
                test_content,
                patch_content_list,
                orig_repro_result,
                patched_repro_list,
            )

            review_thread.save_to_file(
                Path(self.output_dir, f"conv_review_multiple_patch_{round_idx_}.json")
            )

            incorrect_list = [t_idx for t_idx in range(len(patch_content_list)) if t_idx not in correct_list]
            passed_patch_repro = [(patch_content_list[t_idx], patched_repro_list[t_idx]) for t_idx in correct_list]
            unpassed_patch_repro = [(patch_content_list[t_idx], patched_repro_list[t_idx]) for t_idx in incorrect_list]
            passed_patch_repro = deduplicate_patch(passed_patch_repro)
            unpassed_patch_repro = deduplicate_patch(unpassed_patch_repro)

            # todo save all generated results
            all_generated_patch_repro.extend(passed_patch_repro)
            all_generated_patch_repro.extend(unpassed_patch_repro)
            if passed_patch_repro:
                select_idx = int(rank_list[0])
                assert select_idx in correct_list
                selected_patch = patch_content_list[select_idx]
                # todo get and save experience
                cur_exp = {
                    # "issue_description": self.task.get_issue_statement().strip(),
                    "old_patch": old_generated_patch,
                    "old_result": False,
                    "new_patch": selected_patch,
                    'new_result': True
                }

                experiences.append(cur_exp)
                # TODO expand patches
                expand_patch_list = []
                for (temp_patch, temp_patched_repro) in passed_patch_repro:
                    new_patch_list, new_response_list = self.patch_agent._expand_patch(
                        test_content, orig_repro_result, temp_patch, temp_patched_repro
                    )
                    expand_patch_list.extend(new_patch_list)

                    new_patch_list, new_response_list = self.patch_agent._compress_patch(
                        test_content, orig_repro_result, temp_patch, temp_patched_repro
                    )
                    expand_patch_list.extend(new_patch_list)

                # continue filtering
                expand_patch_repro_list = []
                for temp_patch in expand_patch_list:
                    self.save_patch_expand(global_patch_idx, temp_patch)
                    global_patch_idx += 1

                    temp_patched_repro = self.task.execute_test(
                        test_content, temp_patch
                    )
                    expand_patch_repro_list.append((temp_patch, temp_patched_repro))


                all_generated_patch_repro.extend(expand_patch_repro_list)

                with open(Path(self.output_dir,
                               f"result_{len(all_generated_patch_repro)}_patch_{len(test_inputs_list)}_test.jsonl"),
                          'w') as w:
                    for (temp_patch, temp_patched_repro) in all_generated_patch_repro:
                        patch_dict = {'patch_content': temp_patch,
                                      'repro_stdout': temp_patched_repro.stdout, 'repro_stderr': temp_patched_repro.stderr,
                                      'differential_test': []}
                        for test_input_content in test_inputs_list:
                            valid_repro_result = self.task.execute_test(
                                test_input_content, temp_patch
                            )
                            patch_dict['differential_test'].append(
                                {'test': test_input_content, 'stdout': valid_repro_result.stdout, 'stderr': valid_repro_result.stderr}
                            )
                        json.dump(patch_dict, w)
                        w.write('\n')

                # todo saving the experiences
                cur_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(exp_path, "w") as ff:
                    ff.write(json.dumps(
                        {'time': cur_time,
                         "issue_description": self.task.get_issue_statement().strip(),
                         'exps': experiences}
                    ) + "\n")

                yield "0", [item[0] for item in all_generated_patch_repro]
            else:
                if round_idx_ == rounds:
                    print("===============================================================")
                    print(f"test num: {len(test_inputs_list)}, patch num: {len(all_generated_patch_repro)}")
                    print("===============================================================")

                    with open(Path(self.output_dir,
                                   f"result_{len(all_generated_patch_repro)}_patch_{len(test_inputs_list)}_test.jsonl"),
                              'w') as w:
                        for (temp_patch, temp_patched_repro) in all_generated_patch_repro:
                            patch_dict = {'patch_content': temp_patch,
                                          'repro_stdout': temp_patched_repro.stdout,
                                          'repro_stderr': temp_patched_repro.stderr,
                                          'differential_test': []}
                            for test_input_content in test_inputs_list:
                                valid_repro_result = self.task.execute_test(
                                    test_input_content, temp_patch
                                )
                                patch_dict['differential_test'].append(
                                    {'test': test_input_content, 'stdout': valid_repro_result.stdout,
                                     'stderr': valid_repro_result.stderr}
                                )
                            json.dump(patch_dict, w)
                            w.write('\n')

                    yield "0", [item[0] for item in all_generated_patch_repro]

                # select_idx, review_thread = agent_reviewer_v2.review_multiple_patch_select_best(
                #     self.task.repo_name,
                #     issue_statement,
                #     self.patch_agent.bug_locs,
                #     test_content,
                #     [item[0] for item in unpassed_patch_repro],
                #     orig_repro_result,
                #     [item[1] for item in unpassed_patch_repro],
                # )

                select_idx = int(rank_list[0])
                selected_patch = patch_content_list[select_idx]
                selected_patched_repro = patched_repro_list[select_idx]

                # todo get and save experience
                cur_exp = {
                    # "issue_description": self.task.get_issue_statement().strip(),
                    "old_patch": old_generated_patch,
                    "old_result": False,
                    "new_patch": selected_patch,
                    'new_result': False
                }

                experiences.append(cur_exp)

                old_generated_patch = selected_patch

                total_results, review_thread = agent_reviewer.run_patch_with_multiple_review(
                    self.task.repo_name,
                    issue_statement,
                    self.patch_agent.bug_locs,
                    test_content,
                    selected_patch,
                    orig_repro_result,
                    selected_patched_repro,
                )

                patch_content_list = []
                for g_idx, eval_result in enumerate(total_results):
                    if self.use_exps:
                        new_patch_content, new_patch_response = self.patch_agent._refine_patch_W_EXP(
                            test_content, orig_repro_result, selected_patch, selected_patched_repro,
                            round_idx_, g_idx, eval_result
                        )
                    else:
                        new_patch_content, new_patch_response = self.patch_agent._refine_patch(
                            test_content, orig_repro_result, selected_patch, selected_patched_repro,
                            round_idx_, g_idx, eval_result
                        )

                    if new_patch_content and new_patch_response:
                        patch_content_list.append(new_patch_content)


    def save_patch(self, handle: PatchHandle, content: str) -> None:
        Path(self.output_dir, f"extracted_patch_{handle}.diff").write_text(content)

    def save_patch_expand(self, idx, content: str) -> None:
        Path(self.output_dir, f"extracted_expand_patch_{idx}.diff").write_text(content)

    def save_execution_result(
        self, result: ReproResult, patch_handle: str, test_handle: str
    ) -> None:
        Path(
            self.output_dir, f"execution_{patch_handle}_{test_handle}.json"
        ).write_text(
            json.dumps(
                {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                    "triggered": result.reproduced,
                },
                indent=4,
            )
        )

def load_json_exps(exp_dir):
    if not os.path.exists(exp_dir):
        return None

    with open(exp_dir, 'r') as r:
        exps_json = json.load(r)

    return exps_json


def pass_the_test(ori_repro, patched_repro):
    ori_issue_num = ori_repro.stdout.split('Number of test cases confirming the issue exists:')[-1].split(
        'Total number of test cases:')[0]
    try:
        ori_issue_num = int(ori_issue_num.strip())
    except:
        ori_issue_num = 0

    patched_issue_num = patched_repro.stdout.split('Number of test cases confirming the issue exists:')[-1].split(
        'Total number of test cases:')[0]
    try:
        patched_issue_num = int(patched_issue_num.strip())
    except:
        patched_issue_num = -1

    if patched_issue_num == 0 and ori_issue_num > 0:
        return True
    else:
        return False


def write_json_exps(exp_dict, exp_dir):
    with open(exp_dir, 'w') as w:
        json.dump(exp_dict, w)


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


def process_modifications_to_exps(existing_exps_dict, modification_dict):
    # Creating a copy of the existing experiences to avoid modifying the original data
    if not existing_exps_dict:
        updated_exps = {
            "tester_exps": [],
            "coder_exps": [],
        }
    else:
        updated_exps = {
            "tester_exps": existing_exps_dict.get("tester_exps", []).copy(),
            "coder_exps": existing_exps_dict.get("coder_exps", []).copy(),
        }

    # Function to apply the modifications to a specific set of experiences
    def apply_modifications(exps, modifications):
        # Keep track of removed indices to adjust further operations
        removed_indices = set()

        for mod in modifications:
            operation = mod["operation"]
            experience = mod["experience"]
            number = int(mod["number"]) - 1  # Adjust for 0-based indexing

            if operation == "ADD":
                exps.append(experience)
            elif operation == "REMOVE":
                # Adjust the index if the experience has been removed before
                adjusted_number = number - sum(1 for idx in removed_indices if idx < number)
                if 0 <= adjusted_number < len(exps):
                    exps.pop(adjusted_number)

                    removed_indices.add(number)
            elif operation == "EDIT":
                # Adjust the index if the experience has been removed before
                adjusted_number = number - sum(1 for idx in removed_indices if idx < number)
                if 0 <= adjusted_number < len(exps):
                    exps[adjusted_number] = experience

        return exps

    # Apply the modifications to both tester and coder experiences
    updated_exps["tester_exps"] = apply_modifications(updated_exps["tester_exps"], modification_dict.get("tester_exps", []))
    updated_exps["coder_exps"] = apply_modifications(updated_exps["coder_exps"], modification_dict.get("coder_exps", []))[:15]

    return updated_exps


def deduplicate_patch(patch_repro_list):
    new_patch_repro_list = []
    for (patch, repro) in patch_repro_list:
        if patch.strip() not in [item.strip() for (item, _) in new_patch_repro_list]:
            new_patch_repro_list.append((patch, repro))

    return new_patch_repro_list

if __name__ == "__main__":
    pass
