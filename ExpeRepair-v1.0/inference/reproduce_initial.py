import argparse
import json
import os

from tqdm import tqdm

from agents.agent_reproducer import TestAgent
from log import print_banner, print_with_time, print_issue
from model import common
from model.register import register_all_models
from task import SweTask


def reproduce_instance(task, args, repro_result_dict, test_file_path):
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

    # reproduce test generation
    print_banner(f"REPRODUCE TEST GENERATION FOR {instance_id}")
    print_issue(task.get_issue_statement())

    test_agent = TestAgent(task, output_folder, repro_result_dict, test_file_path)

    test_handle, test_content, orig_repro_result = test_agent.write_reproducing_test()
    # test_handle, test_content, orig_repro_result, issue_result = test_agent.write_reproducing_test_with_search()
    if test_content != '' and orig_repro_result != None:
        test_agent.save_test(test_handle)

        test_agent_file = os.path.join(output_folder, f"test_agent_{instance_id}.json")
        with open(test_agent_file, 'w') as f:
            f.write(
                json.dumps(
                    {
                        '_request_idx': test_agent._request_idx,
                        '_responses': test_agent._responses,
                        '_tests': test_agent._tests,
                        '_feedbacks': test_agent._feedbacks,
                        '_history': test_agent._history,
                        '_non_repro_history': test_agent._non_repro_history,
                        '_context': test_agent._context
                    }
                )
            )


        with open(output_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "instance_id": instance_id,
                        "test_content": test_content,
                        "reproduce_stdout": orig_repro_result.stdout,
                        "reproduce_stderr": orig_repro_result.stderr,
                        "returncode": orig_repro_result.returncode,
                        "reproduced": orig_repro_result.reproduced,
                        "reproduction-available": '',
                        "test_type": "reproduce"
                        # 'analysis': issue_result.analysis,
                        # "description": issue_result.description,
                        # "observed_behavior": issue_result.observed_behavior,
                        # "expected_behavior": issue_result.expected_behavior,
                        # "no_reproduction": issue_result.no_reproduction,
                        # "no_context": issue_result.no_context,
                        # 'search_context': test_agent._context
                    }
                )
                + "\n"
            )
    else:
        print(f'================= Failed: {instance_id} ===================')
        with open(output_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "instance_id": instance_id,
                        "test_content": '',
                        "reproduce_stdout": '',
                        "reproduce_stderr": '',
                        "returncode": '',
                        "reproduced": '',
                        "reproduction-available": '',
                        "test_type": ""
                    }
                )
                + "\n"
            )


def reproduce(tasks, args):
    register_all_models()
    common.set_model(args.model)
    # common.set_gpt_model()
    common.set_gpto4_model()
    with open('swe_regression_test.json', 'r') as f:
        regression_dict = json.load(f)

    if args.num_threads == 1:
        for task in tqdm(tasks, colour="MAGENTA"):
            file_list = regression_dict[task.task_id]
            if file_list:
                test_file_path = file_list[0]
            else:
                test_file_path = None
            reproduce_instance(task, args, None, test_file_path=test_file_path)
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
    parser.add_argument("--output_file", type=str, default="reproduce_outputs.jsonl")
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
    # print(tasks)
    # assert 1 == 2
    reproduce(tasks, args)




if __name__ == "__main__":
    main()