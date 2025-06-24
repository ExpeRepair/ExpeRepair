import argparse
import json
import os
import re
from os.path import join as pjoin
from pathlib import Path

from loguru import logger
from tqdm import tqdm

from agentless_utils import (load_test_agent_meta,
                             load_existing_reproduce_test, load_existing_bug_locs, load_existing_search_thread)
from agents.agent_reproducer import TestAgent
from api.review_manage_ase import ReviewManager
from data_structures import ReproResult, BugLocationDirect
from log import print_banner, print_with_time
from model import common
from model.register import register_all_models
from search.search_manage import SearchManager
from task import SweTask, Task


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


def read_latest_traj_json(directory):
    pattern = re.compile(r"traj_(\d+)\.json")
    max_index = -1
    latest_file = None

    # 遍历目录下的文件，找到最大编号的 traj_X.json
    for filename in os.listdir(directory):
        match = pattern.match(filename)
        if match:
            index = int(match.group(1))
            if index > max_index:
                max_index = index
                latest_file = filename

    if latest_file is None:
        return None

    # 读取最新的 JSON 文件内容
    latest_filepath = os.path.join(directory, latest_file)
    with open(latest_filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data


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
    updated_exps["tester_exps"] = apply_modifications(updated_exps["tester_exps"], modification_dict.get("tester_exps", []))[:15]
    updated_exps["coder_exps"] = apply_modifications(updated_exps["coder_exps"], modification_dict.get("coder_exps", []))[:15]

    return updated_exps


def write_json_exps(exp_dict, exp_dir):
    with open(exp_dir, 'w') as w:
        json.dump(exp_dict, w)


def write_patch_iterative_with_review(
        task: Task,
        output_dir: str,
        review_manager: ReviewManager,
        retries=3,
        patch_content_list = []
) -> bool:
    logger.info("Start generating patches with reviewer")
    patch_gen = review_manager.generator(patch_content_list=[])

    eval_summary = None
    for _ in range(retries):
        try:
            patch_handle, patch_content_list = patch_gen.send(eval_summary)
        except StopIteration:
            break

        patch_gen.close()
        if isinstance(patch_content_list, str):
            Path(output_dir, f"final_patch_100.diff").write_text(patch_content_list)
            logger.info(
                "Patch {} passed evaluation. Ending patch generation", patch_handle
            )
        elif isinstance(patch_content_list, list):
            for patch_idx, patch_content in enumerate(patch_content_list):
                Path(output_dir, f"final_patch_{patch_idx}.diff").write_text(patch_content)
                logger.info(
                    "Patch {} passed evaluation. Ending patch generation", patch_idx
                )
        else:
            print(patch_content_list)
            assert 1 == 2

        return True

    return False



def generate_instance(
        task, setup_info, task_info, args, existing_bug_locs, existing_search_thread, existing_reproduce_test,
        repro_result_dict, test_file_path
):
    instance_id = task.task_id


    patch_content_list = []
    #todo

    assert len(existing_bug_locs[instance_id]) == 1
    identified_locs = existing_bug_locs[instance_id][0]
    bug_locs = []
    for idx_i, loc_i in enumerate(identified_locs):
        code_flag = True
        for idx_j, loc_j in enumerate(identified_locs):
            if idx_i != idx_j:
                if loc_i['code'].strip() in loc_j['code'].strip():
                    code_flag = False
                    break

        if code_flag:
            old_code = identified_locs[idx_i]['code'].strip()
            old_code_split = old_code.split('\n')
            if ' def ' in old_code_split[-1] or ' class ' in old_code_split[-1]:
                new_code = '\n'.join(old_code_split[:-1])
            else:
                new_code = old_code

            bug_locs.append(BugLocationDirect(
                rel_file_path=identified_locs[idx_i]['rel_file_path'],
                abs_file_path=identified_locs[idx_i]['abs_file_path'],
                start=identified_locs[idx_i]['start'],
                end=identified_locs[idx_i]['end'],
                class_name=identified_locs[idx_i]['class_name'],
                method_name=identified_locs[idx_i]['method_name'],
                code=new_code,
                intended_behavior=identified_locs[idx_i]['intended_behavior'])
            )

    output_folder = os.path.join(args.output_folder, task.repo_name.split('/')[-1],
                                 f"{instance_id}")
    # output_file = os.path.join(output_folder, args.output_file)

    os.makedirs(output_folder, exist_ok=True)

    dump_meta_data(task.task_id, setup_info, task_info, output_folder)

    # write the arguments
    with open(f"{output_folder}/args.json", "w") as f:
        json.dump(vars(args), f, indent=4)

    if args.task_id is not None:
        if args.task_id != task.task_id:
            return

    task.setup_project()

    test_output_folder =  args.reproduce_folder
    # write patch
    print_banner(f"PATCH GENERATE FOR {instance_id}")
    test_agent = TestAgent(task, test_output_folder, repro_result_dict, test_file_path)
    search_manager = SearchManager(task.project_path, os.path.abspath(output_folder))

    search_msg_thread = existing_search_thread
    # sum_exps = True if generate_idx == 0 else False
    if os.path.exists(os.path.join(args.reproduce_folder, f'test_agent_{instance_id}.json')):
        # assert 1 == 2
        test_agent_meta = load_test_agent_meta(os.path.join(args.reproduce_folder, f'test_agent_{instance_id}.json'))
        test_agent.info_set_up(test_agent_meta)

        coord = ("EMPTY", str(test_agent._request_idx))
        repro_result_map = {coord: ReproResult(stdout=existing_reproduce_test[instance_id]["reproduce_stdout"],
                                               stderr=existing_reproduce_test[instance_id]["reproduce_stderr"],
                                               returncode=existing_reproduce_test[instance_id]["returncode"])}

        review_manager = ReviewManager(
            search_msg_thread,
            bug_locs,
            search_manager,
            task,
            output_folder,
            test_agent,
            repro_result_map,
            use_exps=True,
        )

        write_patch_iterative_with_review(
            task, output_folder, review_manager, patch_content_list=patch_content_list
        )

    else:
        # todo
        print("failed reproduce")

        review_manager = ReviewManager(
            search_msg_thread,
            bug_locs,
            search_manager,
            task,
            output_folder,
            test_agent,
            use_exps=True,
        )

        write_patch_iterative_with_review(
            task, output_folder, review_manager, patch_content_list=patch_content_list
        )



def generate(tasks, all_setup, all_taskinfo, args):
    register_all_models()
    common.set_model(args.model)
    common.set_gpto4_model()
    # common.set_gpto3_model()

    with open('swe_regression_test.json', 'r') as f:
        regression_dict = json.load(f)

    if args.num_threads == 1:
        for task, setup_info, task_info in tqdm(zip(tasks, all_setup, all_taskinfo), colour="MAGENTA"):
            file_list = regression_dict[task.task_id]
            if file_list:
                test_file_path = file_list[0]
            else:
                test_file_path = None

            repo_name = task.repo_name.split('/')[-1]
            args.last_step_reproduce = f'{args.reproduce_folder}/{repo_name}/{task.task_id}/reproduce_outputs.jsonl'
            args.last_step_loc = f'{args.localize_folder}/{repo_name}/{task.task_id}/localize_patch_outputs.jsonl'
            args.last_step_search = f'{args.localize_folder}/{repo_name}/{task.task_id}/search'
            args.reproduce_folder = f'{args.reproduce_folder}/{repo_name}/{task.task_id}'

            existing_reproduce_test = load_existing_reproduce_test(args.last_step_reproduce)
            existing_bug_loc = load_existing_bug_locs(args.last_step_loc)
            existing_search_thread = load_existing_search_thread(args.last_step_search)

            # assert existing_reproduce_test is not None
            assert existing_bug_loc is not None
            assert existing_search_thread is not None

            generate_instance(
                task, setup_info, task_info, args,
                existing_bug_loc, existing_search_thread, existing_reproduce_test,
                None, test_file_path
            )
    else:
        assert 1 == 2


def parse_task_list_file(task_list_file: str) -> list[str]:
    """
    Parse the task list file.
    The file should contain one task/instance id per line, without other characters.
    """
    with open(task_list_file) as f:
        task_ids = f.readlines()
    return [x.strip() for x in task_ids]


def make_swe_tasks(
        task_id: str | None,
        task_list_file: str | None,
        setup_map_file: str,
        tasks_map_file: str
):
    if task_id is not None and task_list_file is not None:
        raise ValueError("Cannot specify both task and task-list.")

    all_task_ids = []
    if task_list_file is not None:
        all_task_ids = parse_task_list_file(task_list_file)
    if task_id is not None:
        all_task_ids = [task_id]
    if len(all_task_ids) == 0:
        raise ValueError("No task ids to run.")

    with open(setup_map_file) as f:
        setup_map = json.load(f)
    with open(tasks_map_file) as f:
        tasks_map = json.load(f)

    # Check if all task ids are in the setup and tasks map
    # This allows failing safely if some tasks are not set up properly
    missing_task_ids = [
        x for x in all_task_ids if not (x in setup_map and x in tasks_map)
    ]
    if missing_task_ids:
        # Log the tasks that are not in the setup or tasks map
        for task_id in sorted(missing_task_ids):
            print_with_time(
                f"Skipping task {task_id} which was not found in setup or tasks map."
            )
        # And drop them from the list of all task ids
        all_task_ids = filter(lambda x: x not in missing_task_ids, all_task_ids)

    all_task_ids = sorted(all_task_ids)

    # for each task in the list to run, create a Task instance
    all_tasks = []
    all_setup = []
    all_taskinfo = []

    for task_id in all_task_ids:
        setup_info = setup_map[task_id]
        task_info = tasks_map[task_id]
        task = SweTask(task_id=task_id,
                       problem_statement=task_info["problem_statement"],
                       repo_path=setup_info["repo_path"],
                       env_name=setup_info["env_name"],
                       pre_install_cmds=setup_info["pre_install"],
                       install_cmd=setup_info["install"],
                       # command to run the relevant tests,
                       test_cmd=setup_info["test_cmd"],
                       commit=task_info["base_commit"],
                       repo_name=task_info["repo"],
                       repo_version=task_info["version"],
                       # modifications to the test suite for this task instance,
                       test_patch=task_info["test_patch"],
                       testcases_passing=task_info["PASS_TO_PASS"],
                       testcases_failing=task_info["FAIL_TO_PASS"]
                       )
        all_tasks.append(task)
        all_setup.append(setup_info)
        all_taskinfo.append(task_info)

    return all_tasks, all_setup, all_taskinfo


def dump_meta_data(task_id, setup_info, task_info, output_dir):
    meta = {
        "task_id": task_id,
        "setup_info": setup_info,
        "task_info": task_info,
    }
    with open(pjoin(output_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=4)
    with open(pjoin(output_dir, "problem_statement.txt"), "w") as f:
        f.write(task_info["problem_statement"])
    with open(pjoin(output_dir, "developer_patch.diff"), "w") as f:
        f.write(task_info["patch"])


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--output_folder", type=str, required=True)
    parser.add_argument("--output_file", type=str, default="generate_outputs.jsonl")
    parser.add_argument("--localize_folder", type=str, required=True)
    parser.add_argument("--reproduce_folder", type=str, required=True)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--num_samples", type=int, default=1)
    parser.add_argument("--task_id", type=str)
    parser.add_argument(
        "--setup-map",
        type=str,
        help="Path to json file that contains the setup information of the projects.",
    )
    parser.add_argument(
        "--tasks-map",
        type=str,
        help="Path to json file that contains the tasks information.",
    )
    parser.add_argument(
        "--task-list-file",
        type=str,
        help="Path to the file that contains all tasks ids to be run.",
    )
    parser.add_argument(
        "--num_threads",
        type=int,
        default=1,
        help="Number of threads to use for creating API requests",
    )
    parser.add_argument(
        "--skip_existing",
        action="store_true",
        help="Skip localization of instance id's which already contain a localization in the output file.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="o4-mini",
    )

    args = parser.parse_args()

    tasks, all_setup, all_taskinfo = make_swe_tasks(
        args.task_id, args.task_list_file, args.setup_map, args.tasks_map
    )

    generate(tasks, all_setup, all_taskinfo, args)


if __name__ == "__main__":
    main()
