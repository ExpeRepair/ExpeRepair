import json
import os
import re
from collections import defaultdict
from openai import OpenAI
from pathlib import Path
import litellm

SYSTEM_PROMPT_W_REPO = "You are an expert software engineering evaluator analyzing patches for resolving GitHub issues in the {repo_name} project."

TASK_PROMPT_INITIAL = '''Since both the reproduction test and candidate patches may be unreliable, you must comprehensively analyze both to identify the correct patch.
Your task is to:
1. Evaluate the Reproduction Test:
   - For each test case, determine whether its input is valid for verifying the patch. A test input is considered invalid if:
     - It would still produce incorrect or error outputs even if the patch is correct and the issue has been resolved.
     - The expected correct behavior of the input is not explicitly described in the issue or cannot be reliably inferred with certainty.
   - If a test is invalid, exclude it from consideration and do not use its result to judge the patch.
   - If a test is valid, determine its expected correct behavior based on the issue description, as well as the functionalities and conventions of the codebase.

2. Evaluate and Score Candidate Patches:
   - For each valid test input, compare the output produced by each patch against the expected correct behavior.
   - Additionally, refer to the pre-patch (original buggy version) output to confirm whether the issue was originally present and whether each patch resolves it.
   - Assess each candidate patch based on the criteria below.
   - Identify correct patches as those that pass all valid test cases.

### Evaluation Criteria:
Bug Fixing Score (0-2):
   0: Incorrect: the patch does not resolve the issue, as it fails all valid tests.
   1: Partially correct: the patch passes some valid tests but not all.
   2: Fully correct: the patch passes all valid tests.

### Analysis Format:
<test_analysis>
  <test_case_number>[Number]</test_case_number>
  <input_valid_analysis>
    [Analysis of whether this test input is valid for issue verification]
    <decision>[valid|invalid]</decision>
  </input_valid_analysis>
</test_analysis>

<patch_analysis>
  <patch_number>[Number]</patch_number>
  <bug_fixing_analysis>
    [Analysis of whether this patch resolves the issue based on valid test results]]
    <score>[0-2]</score>
  </bug_fixing_analysis>
</patch_analysis>


### Final Output Format:
<correct_patch>[List of patch numbers that pass all valid tests]</correct_patch>

Note:
- In the `<correct_patch>` field, include the patch numbers of those that pass all valid tests, e.g., [1]. If no patch qualifies, use an empty list `[]`.
- Ensure that your reasoning explicitly justifies each score and that the final conclusions are logically consistent with your evaluations.
- Make sure to analyze every provided test case and candidate patch without omission.'''


TASK_PROMPT_SECOND = '''Your task is to:
1. Evaluate Tests:
   - For each test case, determine whether its input is valid for verifying the patch. Some test inputs might be invalid, i.e., meaning that even if the patch is correct and the issue is resolved, these tests would still produce incorrect or error outputs.
   - If a test is invalid, exclude it from consideration and do not use its result to judge the patch.
   - If a test is valid, determine its expected correct behavior based on the issue description, as well as the functionalities and conventions of the codebase.
2. Evaluate and Rank Candidate Patches:
   - For each valid test input, compare the output produced by each patch against the expected correct behavior.
   - Assess each candidate patch comprehensively based on the criteria below.
   - Prioritize patches by overall quality.

### Evaluation Criteria:
1. Bug Fixing Score (0-2):
   0: Incorrect: changes do not fix the issue.
   1: Partially correct: changes address some cases but are incomplete.
   2: Fully correct: changes completely fix the issue.

2. Regression Risk (0-2):
   0: High regression risk: changes are likely to introduce unintended side effects or break existing functionality.
   1: Moderate regression risk: some risk remains for side effects in certain scenarios.
   2: Low regression risk: changes are safe, well-contained, and unlikely to cause regressions.

### Analysis Format:
<patch_analysis>
<patch_number>[Number]</patch_number>
<bug_fixing_analysis>
[Analysis of fix approach]
<score>[0-2]</score>
</bug_fixing_analysis>
<regression_risk_analysis>
[Analysis of risks]
<score>[0-2]</score>
</regression_risk_analysis>
</patch_analysis>

### Key Considerations:
1. Effectiveness in resolving the core issue
2. Potential risk of introducing regressions
3. Proper handling of edge cases
4. Compliance with best practices (e.g., simplicity, clarity, and consistent adherence to coding conventions)

### Your analysis should include:
1. Detailed patch changes evaluation
2. Side-by-side comparison
3. Edge case consideration
4. Independent assessment

### Final Output Format:
<rank_patch>[Ranked list of patch numbers from best to worst]</rank_patch>

Note:
- In the `<rank_patch>` filed, list patch numbers ordered by overall quality (higher fixing score and lower regression risk), e.g., [3, 1, 0, 2].
- Ensure that your reasoning explicitly justifies each score and that the final conclusions are logically consistent with your evaluations.
'''



def ase_extract_selection(text: str, tag_list=["rank_patch", "correct_patch"]):
    result = {}

    for tag in tag_list:
        pattern = rf'<{tag}>\[(.*?)\]</{tag}>'
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            raise ValueError(f"Missing or malformed <{tag}>...</{tag}> block.")

        content = match.group(1).strip()
        if content:
            try:
                values = [int(x.strip()) for x in content.split(',')]
            except ValueError:
                raise ValueError(f"Non-integer value found inside <{tag}>...</{tag}> block.")
            result[tag] = values
        else:
            result[tag] = []

    return result


def classify_patches_by_outputs(patch_list):
    """
    Classify patch dicts into categories based on combined outputs of:
    repro_stdout, repro_stderr, and all differential test stdout + stderr.

    Args:
        patch_list (list): List of patch dicts.

    Returns:
        dict: Keys are the concatenated output signatures, values are lists of patch dicts.
    """
    categories = defaultdict(list)

    for patch in patch_list:
        # Build classification key
        combined_output = f"{patch['repro_stdout'].strip()}\n{patch['repro_stderr'].strip()}".strip()
        # for test_result in patch['differential_test']:
        #     combined_output += test_result['stdout'].strip() + test_result['stderr'].strip()

        # Add this patch to its category
        categories[combined_output].append(patch)

    return categories


def read_matching_result_files(directory):
    """
    从指定目录中查找所有形如 `final_patch_X.diff` 的文件，
    返回这些文件的内容列表。

    :param directory: 目录路径
    :return: 匹配文件的内容列表，如果没有文件则返回空列表
    """

    if not os.path.isdir(directory):
        return []
    matching_files_contents = []
    pattern = re.compile(r"result_(\d+)_patch_(\d+)_test\.jsonl")

    # 遍历目录，查找匹配的文件
    for filename in os.listdir(directory):
        match = pattern.match(filename)
        if match:
            file_path = os.path.join(directory, filename)
            with open(file_path, 'r', encoding='utf-8') as f:
                matching_files_contents = f.readlines()

    # 返回所有匹配文件的内容，如果没有文件，则返回空列表
    return matching_files_contents


def borda_count(rank_lists):
    # 输入 rank_lists = [rank_list_1, rank_list_2, rank_list_3]
    # 输出：综合排序后的 patch list
    scores = defaultdict(int)
    N = len(rank_lists[0])
    for rank_list in rank_lists:
        for i, patch in enumerate(rank_list):
            scores[patch] += N - i
    return sorted(scores.keys(), key=lambda x: -scores[x])

def average_rank(rank_lists):
    # 输入 rank_lists = [rank_list_1, rank_list_2, rank_list_3]
    # 输出：综合排序后的 patch list
    ranks = defaultdict(list)
    for rank_list in rank_lists:
        for i, patch in enumerate(rank_list):
            ranks[patch].append(i+1)  # 名次从1开始
    avg_ranks = {patch: sum(pos)/len(pos) for patch, pos in ranks.items()}
    return sorted(avg_ranks.keys(), key=lambda x: avg_ranks[x])


def select_patch_initial(client_name, category_results, test_content,
                         reproduce_stdout, reproduce_stderr, repo_name):
    if client_name == 'client_openai':
        key = os.getenv("OPENAI_KEY")
        model = 'o4-mini'

    elif client_name == 'client_claude':
        key = os.getenv("CLAUDE_KEY")
        model = 'anthropic/claude-sonnet-4-20250514'

    else:
        assert 1 == 2

    system_prompt = SYSTEM_PROMPT_W_REPO.format(repo_name=repo_name)
    test_prompt = (
        f"Here is the reproduction test script and its execution result on the original buggy program, before any patches were applied:\n"
        f"### Test:\n```python\n{test_content.strip()}\n```\n")
    test_prompt += f"### Execution Result:\n{reproduce_stdout.strip()}\n{reproduce_stderr.strip()}"

    result_prompt = "Your colleagues have written several candidate patches. However, after applying each of them, the execution results of the test differ. Below are the execution results obtained by running the provided test script after applying each candidate patch:\n"
    for patch_idx, patch_result in enumerate(category_results):
        result_prompt += f"=== Execution Result for Patch {patch_idx} ===\n{patch_result}".strip() + "\n\n"

    task_prompt = test_prompt.strip() + '\n\n' + result_prompt.strip() + '\n\n' + TASK_PROMPT_INITIAL
    message_list = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_prompt},
    ]

    for _ in range(3):
        try:
            completion = litellm.completion(
                model=model,
                messages=message_list, api_key=key
            )
            response = completion.choices[0].message.content
            select_result = ase_extract_selection(response, ['correct_patch'])
            assert isinstance(select_result, dict)
            correct_idxs = select_result['correct_patch']
            message_list.append({"role": "assistant", "content": response})
            return correct_idxs, message_list

        except Exception as error:
            print(error)

    return '', ''


def select_patch_second(client_name, patch_lines, repo_name):
    if client_name == 'client_openai':
        key = os.getenv("OPENAI_KEY")
        model = 'o4-mini'

    elif client_name == 'client_claude':
        key = os.getenv("CLAUDE_KEY")
        model = 'anthropic/claude-sonnet-4-20250514'

    else:
        assert 1 == 2

    system_prompt = SYSTEM_PROMPT_W_REPO.format(repo_name=repo_name)

    differential_dict = defaultdict(list)
    for patch_idx, patch_line in enumerate(patch_lines):
        patch_content = patch_line['patch_content']
        repro_result = patch_line['repro_stdout'].strip() + '\n' + patch_line['repro_stderr'].strip()

        # assert len(patch_line['differential_test']) == 3
        if patch_line['differential_test']:
            for test_idx, test_line in enumerate(patch_line['differential_test']):
                valid_result = test_line['stdout'].strip() + '\n' + test_line['stderr'].strip()
                differential_dict[test_idx].append({
                    'patch_content': patch_content,
                    'repro_result': repro_result,
                    'valid_result': valid_result
                })
        else:
            differential_dict[0].append({
                'patch_content': patch_content,
                'repro_result': repro_result,
                'valid_result': ""
            })

    all_rank_idxs, all_message_list = [], []
    for test_idx in differential_dict.keys():
        result_prompt = "Your colleagues have written several candidate patches and two test scripts. Below are the execution results obtained by running the two test scripts after applying each candidate patch:\n"

        for patch_idx, patch_dict in enumerate(differential_dict[test_idx]):
            result_prompt += f"=== Patch {patch_idx} ===\n```python\n{patch_dict['patch_content'].strip()}\n```\n"
            result_prompt += f"## Execution Result of the test scrip 1:\n{patch_dict['repro_result'].strip()}\n\n"
            result_prompt += f"## Execution Result of the test scrip 2:\n{patch_dict['valid_result'].strip()}" + '\n\n'


        task_prompt = result_prompt.strip() + '\n\n' + TASK_PROMPT_SECOND
        message_list = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task_prompt},
        ]

        for _ in range(3):
            try:
                completion = litellm.completion(
                    model=model,
                    messages=message_list, api_key=key
                )
                response = completion.choices[0].message.content
                select_result = ase_extract_selection(response, ['rank_patch'])
                assert isinstance(select_result, dict)
                rank_idxs = select_result['rank_patch']
                message_list.append({"role": "assistant", "content": response})
                all_rank_idxs.append(rank_idxs)
                all_message_list.append(message_list)
                break

            except Exception as error:
                print(error)

    return all_rank_idxs, all_message_list



if __name__ == '__main__':
    client_name = 'client_openai'
    # client_name = 'client_claude'
    # client_name = 'client_deepseek'
    repo_list = ['astropy', 'flask', 'pylint', 'pytest', 'requests', 'scikit-learn', 'seaborn', 'sphinx', 'xarray', 'django', 'matplotlib', 'sympy']

    for repo_name in repo_list:
        print('=========================', repo_name, '=================================')
        directory = f"results/generate_example/{repo_name}"
        validation_directory = f"results/validation_example/{repo_name}"

        # for task_id in os.listdir(directory)[:3]:
        for task_id in os.listdir(directory):
            print('Cur Task: ', task_id)
            task_path = validation_directory + '/' + task_id
            if os.path.exists(task_path):
                continue

            Path(task_path).mkdir(parents=True, exist_ok=True)

            test_result_lines = read_matching_result_files(directory + '/' + task_id)
            if not test_result_lines:
                continue

            ### initial select based on the reproduction result
            print("initial select based on the reproduction result")
            test_result_lines = [json.loads(item) for item in test_result_lines]

            # todo Deduplicate based on 'patch_content'
            seen = set()
            deduped_list = []
            for d in test_result_lines:
                content = d['patch_content'].strip()
                if content not in seen:
                    deduped_list.append(d)
                    seen.add(content)

            print(len(test_result_lines), ' deduplicate: ', len(deduped_list))
            test_result_lines = deduped_list

            categories = classify_patches_by_outputs(test_result_lines)
            print(f"Number of categories: {len(categories)}")
            if len(categories) <= 1:
                second_patches = test_result_lines

            else:
                category_items, category_results = {}, []
                for i, (key, group)  in enumerate(categories.items()):
                    category_results.append(key)
                    category_items[i] = group

                with open(directory.replace('generate', 'reproduce') + f'/{task_id}/reproduce_outputs.jsonl', 'r') as f:
                    reproduce_lines = f.readlines()
                assert len(reproduce_lines) == 1

                reproduce_dict = json.loads(reproduce_lines[0])
                test_content = reproduce_dict["test_content"]
                reproduce_stdout = reproduce_dict["reproduce_stdout"]
                reproduce_stderr = reproduce_dict["reproduce_stderr"]

                try:
                    correct_idxs, message_list = select_patch_initial(client_name, category_results,
                                                                      test_content, reproduce_stdout,
                                                                      reproduce_stderr, repo_name)

                    # todo saving the initial selection - message_list
                    assert message_list != ''
                    with open(validation_directory +
                              f'/{task_id}/select_patch_initial.json', 'w') as w:
                        json.dump(message_list, w)
                except Exception as E:
                    print(E)
                    continue

                second_patches = []
                if correct_idxs:
                    for i in correct_idxs:
                        second_patches.extend(category_items[i])
                else:
                    second_patches = test_result_lines

            print("initial selection finish")
            print("second select based on the validation result")
            ### second select based on the validation result
            if len(second_patches) == 1:
                ranked_patch = [second_patches[0]['patch_content']]
            else:
                try:
                    all_rank_idxs, all_message_list = select_patch_second(client_name, second_patches, repo_name)
                    # todo saving the second selection - all_message_list
                    assert all_message_list != []
                    with open(validation_directory +
                              f'/{task_id}/select_patch_second.jsonl', 'w') as w:
                        for message_list in all_message_list:
                            json.dump(message_list, w)
                            w.write('\n')
                except Exception as E:
                    print(E)
                    continue

                # majority voting
                final_ranks = average_rank(all_rank_idxs)
                print(all_rank_idxs)
                print(final_ranks)
                ranked_patch = [second_patches[i]['patch_content'] for i in final_ranks]

            with open(validation_directory +
                      f'/{task_id}/ranked_final_patch.jsonl', 'w') as w:
                for rank_idx, patch in enumerate(ranked_patch):
                    w.write(
                        json.dumps(
                            {
                                "model_name_or_path": f"ase_{rank_idx}",
                                "instance_id": task_id,
                                "model_patch": patch,
                            }
                        )
                        + "\n"
                    )

            print("second selection finish")
