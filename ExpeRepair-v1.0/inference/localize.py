import argparse
import json
import os

from tqdm import tqdm

from agentless_utils import load_existing_reproduce_test, show_project_structure
from log import print_banner, print_with_time
from model import common
from model.register import register_all_models
from repo_structure.preprocess_data import (
    filter_none_python,
    filter_out_test_files,
    get_repo_structure,
)
from search.search_manage import SearchManager
from task import SweTask


def localize_instance(
    task, args, reproduce_result
):
    instance_id = task.task_id

    output_folder = os.path.join(args.output_folder, task.repo_name.split('/')[-1], f"{instance_id}")
    output_file = os.path.join(output_folder, args.output_file)

    os.makedirs(output_folder, exist_ok=True)

    # write the arguments
    with open(f"{output_folder}/args.json", "w") as f:
        json.dump(vars(args), f, indent=4)

    if args.task_id is not None:
        if args.task_id != task.task_id:
            return

    task.setup_project()

    repo_struct = get_repo_structure(
        instance_id, task.repo_name, task.commit, "playground"
    )
    filter_none_python(repo_struct)  # some basic filtering steps
    filter_out_test_files(repo_struct)
    repo_struct = show_project_structure(repo_struct).strip()
    # print(repo_struct)
    # assert 1 == 2

    # bug location
    print_banner(f"BUG LOCALIZATION FOR {instance_id}")
    localize_agent = SearchManager(task.project_path, os.path.abspath(output_folder))

    bug_locs_list = localize_agent.search_locations(
        task, repo_struct, reproduce_result[instance_id]
    )
    written_bug_locs = []
    for bug_locs in bug_locs_list:
        written_bug_locs.append([loc.to_dict() for loc in bug_locs])

    # todo: need further check
    with open(output_file, "w") as f:
        f.write(
            json.dumps(
                {
                    "instance_id": instance_id,
                    "bug_locs": written_bug_locs
                }
            )
            + "\n"
        )



def localize(tasks, args):
    register_all_models()
    common.set_model(args.model)
    common.set_gpto4_model()
    # common.set_gpto3_model()
    if args.num_threads == 1:
        for task in tqdm(tasks, colour="MAGENTA"):
            repo_name = task.repo_name.split('/')[-1]
            reproduce_output = f'{args.reproduce_folder}/{repo_name}/{task.task_id}/reproduce_outputs.jsonl'
            existing_reproduce_test = load_existing_reproduce_test(reproduce_output)
            localize_instance(
                task, args, existing_reproduce_test
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
    tasks_map_file: str,
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
    return all_tasks


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--output_folder", type=str, required=True)
    parser.add_argument("--output_file", type=str, default="localize_patch_outputs.jsonl")
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
        "--model",
        type=str,
        default="o4-mini",
    )

    args = parser.parse_args()

    tasks = make_swe_tasks(
        args.task_id, args.task_list_file, args.setup_map, args.tasks_map
    )

    localize(tasks, args)




if __name__ == "__main__":
    main()