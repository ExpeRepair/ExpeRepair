#!/bin/bash

# List of task_ids
task_ids=("django__django-10914" "django__django-12708")

# Iterate over each task_id and run the command
for task_id in "${task_ids[@]}"; do
    echo "Running task with task_id: $task_id"
    python inference/reproduce_initial.py --model claude-sonnet-4-20250514 \
    --setup-map /home/elloworl/Projects/PycharmProjects/SWE/SWE-bench-ACR/lite_setup_result/setup_map.json \
    --tasks-map /home/elloworl/Projects/PycharmProjects/SWE/SWE-bench-ACR/lite_setup_result/tasks_map.json \
    --output_folder results/reproduce_example --task_id "$task_id"
done