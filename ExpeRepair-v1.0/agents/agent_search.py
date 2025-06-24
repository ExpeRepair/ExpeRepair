import re
from collections import defaultdict

from data_structures import MessageThread
from log import print_acr, print_retrieval
from model import common

GENERAL_SYSTEM_PROMPT_W_REPO = ("As a software developer maintaining the GitHub repository {repo_name}, "
                                "you are responsible for analyzing a submitted issue and identifying the exact location in the repository where the issue occurs.")


SELECT_FILE_PROMPT_INITIAL = """Please review the issue description and thoroughly analyze the issue to uncover all possible causes.
Then, review the file structure and identify TEN files that are most likely to require edits to resolve the issue.

IMPORTANT:
1. Select files ONLY FROM THE PROVIDED LIST and specify their FULL PATHS, starting from the root directory. Do not include any test files.
2. If multiple files share the same name but are in different directories, and the issue description does not specify a particular one, include all relevant ones.
3. Separate each file path with a new line. Wrap the list in triple backticks (```).

Explain your reasoning, then provide your answer in the following format:
```
full_path1/file1.py
full_path2/file2.py
...
```"""


SELECT_FILE_PROMPT_REFINE = """Please review the issue description and thoroughly analyze the issue to uncover all possible causes.
Then, review the skeletons of potentially relevant files and identify up to FOUR files that are most likely to require edits to resolve the issue.

IMPORTANT:
1. Select files ONLY FROM THE PROVIDED FILE LIST and specify their FULL PATHS.
2. The returned files should be separated by new lines ordered by most to least important. Wrap the file list in triple backticks (```).

Explain your reasoning, then provide your answer in the following format:
```
full_path1/file1.py
full_path2/file2.py
```"""


SELECT_EDIT_PROMPT_INITIAL = """Your task is to:
1. Carefully review the issue description and thoroughly analyze the issue to uncover all possible causes.
2. Review the provided code file and identify at least three locations within the file that require inspection or modification to resolve the issue.

Guidelines for Specifying Locations:
1. The locations can be specified as class names, function or method names, or exact line numbers.
2. Line ranges may span across multiple classes or methods if necessary. Ensure all line numbers are accurate and within the valid range of the provided code.

IMPORTANT:
1. Analyze how the provided code file relates to the issue and identify at least three related locations that require modification (e.g., edits, additions) or require inspection (e.g., provide important context).
2. If a new method or class needs to be added to resolve the issue, specify a broader line range that includes surrounding code to provide context and ensure proper placement.
3. Check if any dependencies, helper methods, or global variables should be modified to ensure logical and structural coherence.
4. Check the functionally similar or similarly named methods/classes that might have the same bug or require consistency updates.

Provide the response using the following strict format:
<analysis>Explain your reasoning step by step</analysis>

<location>
  <class>ClassName1</class>
  <method></method>
  <start_line>124</start_line>
  <end_line>189</end_line>
</location>

<location>
  <class></class>
  <method>function_name1</method>
  <start_line></start_line>
  <end_line></end_line>
</location>

<location>
  <class></class>
  <method></method>
  <start_line>751</start_line>
  <end_line>1126</end_line>
</location>

..."""


SELECT_EDIT_PROMPT_REFINE = """Please review the issue description and thoroughly analyze the issue to uncover all possible causes.
Then, review the provided code snippets, identify all related locations that require inspection or modification to resolve the issue.

Guidelines for Specifying Locations:
1. The locations can be specified as class names, function or method names, or exact line numbers.
2. Line ranges may span across multiple classes or methods if necessary. Ensure all line numbers are accurate and within the valid range of the provided code.

IMPORTANT:
1. Do not recommend any edits or propose fixes. Your task is solely to identify all related locations — including those requiring direct edits, additions, or providing important contextual information — without overlooking any.
2. If a new method or class needs to be added to resolve the issue, specify a broader line range that includes surrounding code to provide context and ensure proper placement.
3. Check if any dependencies, helper methods, or global variables should be modified to ensure logical and structural coherence.
4. Check the functionally similar or similarly named methods/classes that might have the same bug or require consistency updates.

Provide the response using the following strict format:
<analysis>Explain your reasoning step by step</analysis>

<file>
  <path>FilePath1</path>
  <location>
    <class>ClassName1</class>
    <method></method>
    <start_line>124</start_line>
    <end_line>189</end_line>
  </location>
  <location>
    <class></class>
    <method></method>
    <start_line>301</start_line>
    <end_line>402</end_line>
  </location>
</file>

<file>
  <path>FilePath2</path>
  <location>
    <class></class>
    <method>method_name</method>
    <start_line>15</start_line>
    <end_line>37</end_line>
  </location>
  <location>
    <class></class>
    <method></method>
    <start_line>753</start_line>
    <end_line>1126</end_line>
  </location>
</file>

..."""



# TODO: move this to some util class, since other agents may need it as well
def prepare_issue_prompt(problem_stmt: str) -> str:
    """
    Given the raw problem statement, sanitize it and prepare the issue prompt.
    Args:
        problem_stmt (str): The raw problem statement.
            Assumption: the problem statement is the content of a markdown file.
    Returns:
        str: The issue prompt.
    """
    # remove markdown comments
    problem_wo_comments = re.sub(r"<!--.*?-->", "", problem_stmt, flags=re.DOTALL)
    content_lines = problem_wo_comments.split("\n")
    # remove spaces and empty lines
    content_lines = [x.strip() for x in content_lines]
    content_lines = [x for x in content_lines if x != ""]
    problem_stripped = "\n".join(content_lines)
    # add tags
    result = ("<issue>\n" + problem_stripped.strip() + "\n</issue>")
    return result


def prepare_issue_prompt_wo_tag(problem_stmt: str) -> str:
    """
    Given the raw problem statement, sanitize it and prepare the issue prompt.
    Args:
        problem_stmt (str): The raw problem statement.
            Assumption: the problem statement is the content of a markdown file.
    Returns:
        str: The issue prompt.
    """
    # remove markdown comments
    problem_wo_comments = re.sub(r"<!--.*?-->", "", problem_stmt, flags=re.DOTALL)
    content_lines = problem_wo_comments.split("\n")
    # remove spaces and empty lines
    content_lines = [x.strip() for x in content_lines]
    content_lines = [x for x in content_lines if x != ""]
    problem_stripped = "\n".join(content_lines)
    # add tags
    result = problem_stripped.strip()
    return result



def locate_files_initial(
    task, repo_struc, reproducer_result
):
    """
    Args:
        - issue_stmt: problem statement
        - sbfl_result: result after running sbfl
    """

    msg_thread = MessageThread()
    msg_thread.add_system(GENERAL_SYSTEM_PROMPT_W_REPO.format(repo_name=task.repo_name))

    issue_prompt = "Here is the issue description:\n"
    issue_prompt += prepare_issue_prompt_wo_tag(task.get_issue_statement())
    msg_thread.add_user(issue_prompt)

    # if reproducer_result is not None:
    #     assert reproducer_result['test_content'] != ''
    #     # reproducer_prompt = ("Your colleague has provided a reproduction script to replicate the issue. "
    #     #                      "Analyzing its execution results may help you pinpoint the locations in the codebase where the bug originates.\n")
    #     # reproducer_prompt += f"Execution Results:\n### Standard output:\n{reproducer_result['reproduce_stdout'].strip()}\n"
    #     # reproducer_prompt += f"### Standard error:\n{reproducer_result['reproduce_stderr'].strip()}\n"
    #     # msg_thread.add_user(reproducer_prompt)
    #
    #     reproduction_prompt = "Below is the reproduction script written by your colleague, along with its execution results on the original buggy program:\n"
    #     reproduction_prompt += f"```python\n{reproducer_result['test_content']}\n```\n"
    #     reproduction_prompt += f"Execution Results:\nSTDOUT:\n{reproducer_result['reproduce_stdout'].strip()}\nSTDERR:\n{reproducer_result['reproduce_stderr'].strip()}"
    #     msg_thread.add_user(reproduction_prompt)

    structure_prompt = f"Here is the project structure:\n"
    structure_prompt += repo_struc
    msg_thread.add_user(structure_prompt)

    msg_thread.add_user(SELECT_FILE_PROMPT_INITIAL)
    print_acr(SELECT_FILE_PROMPT_INITIAL, "context retrieval for relevant files")

    relevant_files = set()
    for _ in range(1, 4):
        response, *_ = common.SELECTED_MODEL.call(msg_thread.to_msg())
        file_list = convert_response_to_patch(response)
        if not file_list:
            continue
        else:
            msg_thread.add_model(response)
            for file in file_list.strip().split("\n"):
                if '```' not in file:
                    relevant_files.add(file.strip())

            break

    relevant_files = list(relevant_files)
    assert relevant_files != []

    # files, classes, functions = get_full_file_paths_and_classes_and_functions(repo_struc)
    #
    # relevant_files = correct_file_paths(relevant_files, files)

    print_retrieval('\n\n'.join(relevant_files), "Model response (Relevant Files Initial)")
    assert relevant_files != []

    # todo model iterative check
    return relevant_files, msg_thread


def locate_files_refine(
    task, relevant_file_skeletons, reproducer_result
):
    """
    Args:
        - issue_stmt: problem statement
        - sbfl_result: result after running sbfl
    """
    msg_thread = MessageThread()
    msg_thread.add_system(GENERAL_SYSTEM_PROMPT_W_REPO.format(repo_name=task.repo_name))

    issue_prompt = "Here is the issue description:\n"
    issue_prompt += prepare_issue_prompt_wo_tag(task.get_issue_statement())
    msg_thread.add_user(issue_prompt)

    # if reproducer_result is not None:
    #     assert reproducer_result['test_content'] != ''
    #     # reproducer_prompt = ("Your colleague has provided a reproduction script to replicate the issue. "
    #     #                      "Analyzing its execution results may help you pinpoint the locations in the codebase where the bug originates.\n")
    #     # reproducer_prompt += f"Execution Results:\n### Standard output:\n{reproducer_result['reproduce_stdout'].strip()}\n"
    #     # reproducer_prompt += f"### Standard error:\n{reproducer_result['reproduce_stderr'].strip()}\n"
    #     # msg_thread.add_user(reproducer_prompt)
    #
    #     reproduction_prompt = "Below is the reproduction script written by your colleague, along with its execution results on the original buggy program:\n"
    #     reproduction_prompt += f"```python\n{reproducer_result['test_content']}\n```\n"
    #     reproduction_prompt += f"Execution Results:\nSTDOUT:\n{reproducer_result['reproduce_stdout'].strip()}\nSTDERR:\n{reproducer_result['reproduce_stderr'].strip()}"
    #     msg_thread.add_user(reproduction_prompt)

    structure_prompt = "Here are the skeletons of the files:\n"
    structure_prompt += relevant_file_skeletons
    msg_thread.add_user(structure_prompt)

    msg_thread.add_user(SELECT_FILE_PROMPT_REFINE)
    print_acr(SELECT_FILE_PROMPT_REFINE, "context retrieval for relevant files")

    relevant_files = set()
    for _ in range(1, 4):
        response, *_ = common.GPTo4_MODEL.call(msg_thread.to_msg())
        file_list = convert_response_to_patch(response)
        if not file_list:
            continue
        else:
            print(response)
            msg_thread.add_model(response)
            for file in file_list.strip().split("\n"):
                if '```' not in file:
                    relevant_files.add(file.strip())

            break

    relevant_files = list(relevant_files)
    assert relevant_files != []

    # files, classes, functions = get_full_file_paths_and_classes_and_functions(repo_struc)
    #
    # relevant_files = correct_file_paths(relevant_files, files)

    print_retrieval('\n\n'.join(relevant_files), "Model response (Relevant Files Refine)")
    assert relevant_files != []

    # assert 1 == 2

    # todo model iterative check
    return relevant_files, msg_thread


def locate_edits4file(
    task, relevant_elements, reproducer_result
):
    """
    Args:
        - issue_stmt: problem statement
        - sbfl_result: result after running sbfl
    """

    msg_thread = MessageThread()
    msg_thread.add_system(GENERAL_SYSTEM_PROMPT_W_REPO.format(repo_name=task.repo_name))

    issue_prompt = "Here is the issue description:\n"
    issue_prompt += prepare_issue_prompt_wo_tag(task.get_issue_statement())
    msg_thread.add_user(issue_prompt)

    # if reproducer_result is not None:
    #     assert reproducer_result['test_content'] != ''
    #     # reproducer_prompt = ("Your colleague has provided a reproduction script to replicate the issue. "
    #     #                      "Analyzing its execution results may help you pinpoint the locations in the codebase where the bug originates.\n")
    #     # reproducer_prompt += f"Execution Results:\n### Standard output:\n{reproducer_result['reproduce_stdout'].strip()}\n"
    #     # reproducer_prompt += f"### Standard error:\n{reproducer_result['reproduce_stderr'].strip()}\n"
    #     # msg_thread.add_user(reproducer_prompt)
    #
    #     reproduction_prompt = "Below is the reproduction script written by your colleague, along with its execution results on the original buggy program:\n"
    #     reproduction_prompt += f"```python\n{reproducer_result['test_content']}\n```\n"
    #     reproduction_prompt += f"Execution Results:\nSTDOUT:\n{reproducer_result['reproduce_stdout'].strip()}\nSTDERR:\n{reproducer_result['reproduce_stderr'].strip()}"
    #     msg_thread.add_user(reproduction_prompt)

    structure_prompt = "Here is the project file that may be relevant to this issue:\n"
    structure_prompt += relevant_elements
    msg_thread.add_user(structure_prompt)

    msg_thread.add_user(SELECT_EDIT_PROMPT_INITIAL)
    print_acr(SELECT_EDIT_PROMPT_INITIAL, "context retrieval for relevant edits")

    for _ in range(3):
        response, *_ = common.GPTo4_MODEL.call(msg_thread.to_msg())
        relevant_edits = extract_locations_initial(response)
        if not relevant_edits:
            continue
        else:
            msg_thread.add_model(response)
            print_retrieval(str(relevant_edits), "Model response (Relevant Edits)")
            return relevant_edits, msg_thread

    assert 1 == 2


def locate_edits4refine(
    task, relevant_elements, reproducer_result
):
    """
    Args:
        - issue_stmt: problem statement
        - sbfl_result: result after running sbfl
    """

    msg_thread = MessageThread()
    msg_thread.add_system(GENERAL_SYSTEM_PROMPT_W_REPO.format(repo_name=task.repo_name))

    issue_prompt = "Here is the issue description:\n"
    issue_prompt += prepare_issue_prompt_wo_tag(task.get_issue_statement())
    msg_thread.add_user(issue_prompt)

    # if reproducer_result is not None:
    #     assert reproducer_result['test_content'] != ''
    #     # reproducer_prompt = ("Your colleague has provided a reproduction script to replicate the issue. "
    #     #                      "Analyzing its execution results may help you pinpoint the locations in the codebase where the bug originates.\n")
    #     # reproducer_prompt += f"Execution Results:\n### Standard output:\n{reproducer_result['reproduce_stdout'].strip()}\n"
    #     # reproducer_prompt += f"### Standard error:\n{reproducer_result['reproduce_stderr'].strip()}\n"
    #     # msg_thread.add_user(reproducer_prompt)
    #
    #     reproduction_prompt = "Below is the reproduction script written by your colleague, along with its execution results on the original buggy program:\n"
    #     reproduction_prompt += f"```python\n{reproducer_result['test_content']}\n```\n"
    #     reproduction_prompt += f"Execution Results:\nSTDOUT:\n{reproducer_result['reproduce_stdout'].strip()}\nSTDERR:\n{reproducer_result['reproduce_stderr'].strip()}"
    #     msg_thread.add_user(reproduction_prompt)

    structure_prompt = "Here are the potentially relevant code snippets:\n"
    structure_prompt += relevant_elements
    msg_thread.add_user(structure_prompt)

    msg_thread.add_user(SELECT_EDIT_PROMPT_REFINE)
    print_acr(SELECT_EDIT_PROMPT_REFINE, "context retrieval for relevant edits")

    for _ in range(3):
        response, *_ = common.SELECTED_MODEL.call(msg_thread.to_msg())
        relevant_edits = extract_locations_refine(response)
        if not relevant_edits:
            print('error!!!!!!!!!!!!!!!!!!!!!!!!!!!')
            print(response)
            continue
        else:
            msg_thread.add_model(response)
            print_retrieval(str(relevant_edits), "Model response (Relevant Edits)")
            return relevant_edits, msg_thread

    assert 1 == 2


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


def convert_response_to_patch(response: str):
    blocks = extract_markdown_code_blocks(response)

    if len(blocks) == 1:
        return blocks[0]
    else:
        return None



def extract_locations_initial(form_response: str):
    """
    Extract location information from a structured response string.

    Args:
        form_response (str): The response text containing <location> blocks.

    Returns:
        List[dict]: A list of dicts containing 'class', 'method', 'start_line', and 'end_line'.
    """
    location_pattern = re.compile(
        r"<location>\s*"
        r"<class>(.*?)</class>\s*"
        r"<method>(.*?)</method>\s*"
        r"<start_line>(.*?)</start_line>\s*"
        r"<end_line>(.*?)</end_line>\s*"
        r"</location>",
        re.DOTALL
    )

    locations = []
    for match in location_pattern.finditer(form_response):
        class_name, method_name, start_line, end_line = match.groups()
        location_dict = {
            "class": class_name.strip(),
            "method": method_name.strip(),
            "start_line": start_line.strip(),
            "end_line": end_line.strip()
        }
        locations.append(location_dict)

    return locations


def extract_locations_refine(response):
    file_blocks = re.findall(r"<file>(.*?)</file>", response, re.DOTALL)
    result = defaultdict(list)

    for file_block in file_blocks:
        file_path_match = re.search(r"<path>(.*?)</path>", file_block)
        if not file_path_match:
            continue
        file_path = file_path_match.group(1).strip()

        locations = re.findall(r"<location>(.*?)</location>", file_block, re.DOTALL)
        for loc in locations:
            class_name = re.search(r"<class>(.*?)</class>", loc)
            method_name = re.search(r"<method>(.*?)</method>", loc)
            start_line = re.search(r"<start_line>(.*?)</start_line>", loc)
            end_line = re.search(r"<end_line>(.*?)</end_line>", loc)

            location_dict = {
                "class": class_name.group(1).strip() if class_name else "",
                "method": method_name.group(1).strip() if method_name else "",
                "start_line": start_line.group(1).strip() if start_line else "",
                "end_line": end_line.group(1).strip() if end_line else "",
            }
            result[file_path].append(location_dict)

    return dict(result)
