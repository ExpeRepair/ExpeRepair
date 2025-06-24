from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum

from agents.agent_common import InvalidLLMResponse
from data_structures import MessageThread, BugLocation
from model import common

SYSTEM_PROMPT_W_REPO = (
    "You are an experienced software engineer responsible for maintaining the GitHub project {repo_name}.\n"
    "An issue has been submitted.\n"
    "Engineer A has written a test script designed to reproduce the issue. "
    "Engineer B has written a patch to resolve the issue.\n"
    "Your task is to assess whether the patch fully resolves the issue.\n"
    "NOTE: both the test and the patch may be wrong."
)



SYSTEM_SELECT_PROMPT_W_REPO = (
    "You are an experienced software engineer responsible for maintaining the GitHub project {repo_name}.\n"
    "An issue has been submitted.\n"
    "Engineer A has provided a reproduction test script intended to reproduce and demonstrate the issue.\n"
    "Engineer B has proposed several candidate patches to resolve the issue.\n"
    "Your task is to evaluate the candidate patches and select the best one."
)



REVIEW_PROMPT_INITIAL = '''Since both the reproduction test and candidate patches may be wrong, you must comprehensively analyze both - do not judge patches solely based on test execution results.
Your task is to:
1. Evaluate the Reproduction Test:
   - For each test case, determine whether its input is valid for verifying the patch. Some test inputs might be invalid, i.e., meaning that even if the patch is correct and the issue is resolved, these tests would still produce incorrect or error outputs.
   - If a test is invalid, exclude it from consideration and do not use its result to judge the patch.
   - If valid, determine the expected correct behavior based on the issue description.
2. Evaluate Candidate Patches:
   - For each valid test input, compare the output produced by each patch against the expected correct behavior.
   - Double check: refer to the pre-patch output to confirm the issue's presence and whether it has been resolved. 
3. Score and Rank Patches:
   - Assess each candidate patch comprehensively based on the criteria below.
   - Prioritize patches by overall quality and identify those that fully resolve the core issue.

### Evaluation Criteria:
Bug Fixing Score (0-2):
   0: Incorrect: changes do not fix the issue.
   1: Partially correct: changes address some cases but are incomplete.
   2: Fully correct: changes completely fix the issue.

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
    [Analysis of whether this patch resolves the core issue]
    <score>[0-2]</score>
  </bug_fixing_analysis>
</patch_analysis>

### Patch Quality Considerations:
1. Effectiveness in resolving the core issue
2. Handling of edge cases
3. Implementation quality
4. Potential regression risks

### Final Output Format:
<rank_patch>[Ranked list of patch numbers from best to worst]</rank_patch>
<correct_patch>[List of patch numbers that are fully resolves the issue]</correct_patch>

Note:
- In the `<rank_patch>` filed, list patch numbers ordered by overall quality, e.g., [3, 1, 0, 2].
- In the `<correct_patch>` filed, include patch numbers with a **bug fixing score of 2**. If none qualify, use [].
- Ensure that your reasoning fully justifies each score and that the final rankings are logically consistent with your evaluations.
'''


REFINE_THINK_PROMPT = """The candidate patch appears to be insufficient in fully resolving the issue, as the execution results indicate that the issue persists even after applying the patch.
Your task is to carefully analyze the patch and provide detailed, actionable suggestions for improving the patch's correctness and reliability in resolving the issue.

### Steps:
1. Review the issue description to understand the core problem and determine the expected correct behavior for each test case after the issue is resolved.
2. Examine the candidate patch to understand its intended fix strategy and how it attempts to address the issue.
3. Compare the execution results before and after applying the patch. Identify which errors, test failures, or unexpected behaviors persist and why the patch failed to resolve them.
4. For the tests that the patch failed, propose targeted and actionable suggestions:
   - You may suggest improvements to the existing patch or propose alternative fixes at different locations if necessary.
   - Suggestions must aim to make a **meaningful, functional improvement** to the candidate patch.
   - Avoid superficial changes such as documentation edits, unrelated code refactoring, or restating existing patches.
   - When possible, ensure suggestions align with the project's existing coding style and structure.

Wrap your analysis process in <patch_analysis> tags, and provide the suggestions in <patch_advice> tags.
### Output Format:
<patch_analysis>...</patch_analysis>
<patch_advice>...</patch_advice>
"""


INITIAL_REQUEST = ()


@dataclass
class Review:
    patch_decision: ReviewDecision
    patch_analysis: str
    patch_advice: str
    test_decision: ReviewDecision
    test_analysis: str
    test_advice: str

    def __str__(self):
        return (
            f"Patch decision: {self.patch_decision.value}\n\n"
            f"Patch analysis: {self.patch_analysis}\n\n"
            f"Patch advice: {self.patch_advice}\n\n"
            f"Test decision: {self.test_decision.value}\n\n"
            f"Test analysis: {self.test_analysis}\n\n"
            f"Test advice: {self.test_advice}"
        )

    def to_json(self):
        return {
            "patch-correct": self.patch_decision.value,
            "patch-analysis": self.patch_analysis,
            "patch-advice": self.patch_advice,
            "test-correct": self.test_decision.value,
            "test-analysis": self.test_analysis,
            "test-advice": self.test_advice,
        }


class ReviewDecision(Enum):
    YES = "yes"
    NO = "no"


def extract_review_result_claude(content: str) -> Review | None:
    try:
        data = extract_json_from_string(content)

        review = Review(
            patch_decision=ReviewDecision(data["patch-correct"].lower()),
            patch_analysis=data["patch-analysis"],
            patch_advice=data["patch-advice"],
            test_decision=ReviewDecision(data["test-correct"].lower()),
            test_analysis=data["test-analysis"],
            test_advice=data["test-advice"],
        )

        if (
            (review.patch_decision == ReviewDecision.NO) and not review.patch_advice
        ) and ((review.test_decision == ReviewDecision.NO) and not review.test_advice):
            return None

        return review

    except Exception:
        return None



def extract_json_from_string(input_str):
    """
    Extract JSON data wrapped with triple backticks (```json ... ``` or ``` ... ```).

    Parameters:
        input_str (str): The input string containing JSON data wrapped with triple backticks.

    Returns:
        dict: Parsed JSON data if valid JSON is found, otherwise None.
    """
    # Use regex to find JSON content wrapped with triple backticks, optionally prefixed by "json"
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", input_str, re.DOTALL)

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


def review_multiple_patch_select_best(
    repo_name: str,
    issue_statement: str,
    bug_locs,
    test: str,
    patch_list,
    orig_repro,
    patched_repro_list,
    retries: int = 5,
):
    prefix_thread = MessageThread()
    prefix_thread.add_system(SYSTEM_SELECT_PROMPT_W_REPO.format(repo_name=repo_name))

    issue_prompt = ("Here is the issue:\n" +
                    "<issue>\n" + issue_statement.strip() + "\n</issue>")
    prefix_thread.add_user(issue_prompt)

    # location_prompt = ("Here are the possible buggy locations:\n" +
    #                    BugLocation.multiple_locs_to_str_for_model_wo_intention(bug_locs))
    # prefix_thread.add_user(location_prompt)

    test_prompt = f"Here is the reproduction test written by Engineer A:\n### Test:\n```python\n{test.strip()}\n```\n"

    test_prompt += (
        "Here is the result of executing the test on the original buggy program, before any patches were applied:\n"
        f"### stdout:\n{orig_repro.stdout.strip()}\n"
        f"### stderr:\n{orig_repro.stderr.strip()}\n\n"
    )

    # prefix_thread.add_user(test_prompt)

    patch_prompt = f"Here are the candidate patches written by Engineer B and the execution results from running the reproduction test after applying them:\n"
    for patch_idx, (patch, patch_repro) in enumerate(zip(patch_list, patched_repro_list)):
        patch_prompt += f"### Patch {patch_idx}:\n```python\n{patch.strip()}\n```\n"
        patch_prompt += (f"### stdout:\n{patch_repro.stdout.strip()}\n" +
                         f"### stderr:\n{patch_repro.stderr.strip()}\n\n")

    prefix_thread.add_user(test_prompt + patch_prompt.strip())

    prefix_thread.add_user(REVIEW_PROMPT_INITIAL)

    for try_idx in range(1, retries + 1):
        response, *_ = common.GPTo4_MODEL.call(
            prefix_thread.to_msg()
        )
        print(response)

        try:
            select_result = ase_extract_selection(response)
            assert isinstance(select_result, dict)
            rank_patch = select_result['rank_patch']
            correct_patch = select_result['correct_patch']

            prefix_thread.add_model(response)
            return rank_patch, correct_patch, prefix_thread
        except Exception as err:
            print('Wrong:', err)

    raise InvalidLLMResponse(f"failed to review in {retries} attempts")




def run_patch_with_multiple_review(
    repo_name: str,
    issue_statement: str,
    bug_locs,
    test: str,
    patch,
    orig_repro,
    patched_repro,
    review_num = 4
):
    prefix_thread = MessageThread()
    prefix_thread.add_system(SYSTEM_PROMPT_W_REPO.format(repo_name=repo_name))

    issue_prompt = ("Here is the issue:\n" +
                    "<issue>\n" + issue_statement.strip() + "\n</issue>")
    prefix_thread.add_user(issue_prompt)

    location_prompt = ("Here are the possible buggy locations:\n" +
                       BugLocation.multiple_locs_to_str_for_model_wo_intention(bug_locs))
    prefix_thread.add_user(location_prompt)

    test_prompt = (f"Here is the reproduction test script and its execution result on the original buggy program, before any patches were applied:\n"
                   f"### Test:\n```python\n{test.strip()}\n```\n")
    test_prompt += (f"### stdout:\n{orig_repro.stdout.strip()}\n" +
                     f"### stderr:\n{orig_repro.stderr.strip()}\n\n")

    # prefix_thread.add_user(test_prompt)

    patch_prompt = f"Here is the patch and the execution results from running the above reproduction script after applying it:\n"
    patch_prompt += f"### Patch:\n```python\n{patch.strip()}\n```\n"
    patch_prompt += (f"### stdout:\n{patched_repro.stdout.strip()}\n" +
                     f"### stderr:\n{patched_repro.stderr.strip()}\n\n")

    prefix_thread.add_user(test_prompt + patch_prompt.strip())

    prefix_thread.add_user(REFINE_THINK_PROMPT)
    # claude 3.5, o1-mini, o3-mini, deepseek-v3
    total_response, total_result = [], []
    for try_idx in range(5):
        response, *_ = common.SELECTED_MODEL.call(
            prefix_thread.to_msg()
        )
        print('SELECTED_MODEL:\n', response)
        eval_result = ase_extract_result(response)

        if not isinstance(eval_result, dict):
            print("InvalidLLMResponse")
            continue
        else:
            total_response.append(response)
            total_result.append(eval_result)
            break

    for try_idx in range(5):
        response, *_ = common.GPTo4_MODEL.call(
            prefix_thread.to_msg()
        )
        print('GPTo4_MODEL:\n', response)
        eval_result = ase_extract_result(response)

        if not isinstance(eval_result, dict):
            print("InvalidLLMResponse")
            continue
        else:
            total_response.append(response)
            total_result.append(eval_result)
            break

    for try_idx in range(5):
        response, *_ = common.GPTo4_MODEL.call(
            prefix_thread.to_msg()
        )
        print('GPTo4_MODEL:\n', response)
        eval_result = ase_extract_result(response)

        if not isinstance(eval_result, dict):
            print("InvalidLLMResponse")
            continue
        else:
            total_response.append(response)
            total_result.append(eval_result)
            break

    for try_idx in range(5):
        response, *_ = common.SELECTED_MODEL.call(
            prefix_thread.to_msg()
        )
        print('Deepseek_MODEL:\n', response)
        eval_result = ase_extract_result(response)

        if not isinstance(eval_result, dict):
            print("InvalidLLMResponse")
            continue
        else:
            total_response.append(response)
            total_result.append(eval_result)
            break

    for response in total_response:
        prefix_thread.add_model(response)

    return total_result, prefix_thread



def ase_extract_result(text):
    """
    Extracts content from <patch_analysis>, <patch_correct>, and <patch_advice> tags
    and returns a dict in the desired format.
    """
    if '<patch_analysis>' in text and '</patch_analysis>' in text and '<patch_advice>' in text and '</patch_advice>' not in text:
        text = text + '</patch_advice>'

    result = {
        "patch_analysis": "",
        "patch_advice": ""
    }

    # Define regex patterns for each tag (non-greedy, DOTALL for multiline)
    patterns = {
        "patch_analysis": r"<patch_analysis>(.*?)</patch_analysis>",
        "patch_advice": r"<patch_advice>(.*?)</patch_advice>"
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if not match:
            return None  # If any tag is missing/incomplete, return None
        result[key] = match.group(1).strip()

    return result


def ase_extract_selection(text: str):
    result = {}

    for tag in ["rank_patch", "correct_patch"]:
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

if __name__ == "__main__":
    pass

#     # setup before test

#     register_all_models()
#     common.set_model("gpt-4-0125-preview")

#     # TEST
#     instance_id = "matplotlib__matplotlib-23299"

#     problem_stmt = "[Bug]: get_backend() clears figures from Gcf.figs if they were created under rc_context\n### Bug summary\r\n\r\ncalling `matplotlib.get_backend()` removes all figures from `Gcf` if the *first* figure in `Gcf.figs` was created in an `rc_context`.\r\n\r\n### Code for reproduction\r\n\r\n```python\r\nimport matplotlib.pyplot as plt\r\nfrom matplotlib import get_backend, rc_context\r\n\r\n# fig1 = plt.figure()  # <- UNCOMMENT THIS LINE AND IT WILL WORK\r\n# plt.ion()            # <- ALTERNATIVELY, UNCOMMENT THIS LINE AND IT WILL ALSO WORK\r\nwith rc_context():\r\n    fig2 = plt.figure()\r\nbefore = f'{id(plt._pylab_helpers.Gcf)} {plt._pylab_helpers.Gcf.figs!r}'\r\nget_backend()\r\nafter = f'{id(plt._pylab_helpers.Gcf)} {plt._pylab_helpers.Gcf.figs!r}'\r\n\r\nassert before == after, '\\n' + before + '\\n' + after\r\n```\r\n\r\n\r\n### Actual outcome\r\n\r\n```\r\n---------------------------------------------------------------------------\r\nAssertionError                            Traceback (most recent call last)\r\n<ipython-input-1-fa4d099aa289> in <cell line: 11>()\r\n      9 after = f'{id(plt._pylab_helpers.Gcf)} {plt._pylab_helpers.Gcf.figs!r}'\r\n     10 \r\n---> 11 assert before == after, '\\n' + before + '\\n' + after\r\n     12 \r\n\r\nAssertionError: \r\n94453354309744 OrderedDict([(1, <matplotlib.backends.backend_qt.FigureManagerQT object at 0x7fb33e26c220>)])\r\n94453354309744 OrderedDict()\r\n```\r\n\r\n### Expected outcome\r\n\r\nThe figure should not be missing from `Gcf`.  Consequences of this are, e.g, `plt.close(fig2)` doesn't work because `Gcf.destroy_fig()` can't find it.\r\n\r\n### Additional information\r\n\r\n_No response_\r\n\r\n### Operating system\r\n\r\nXubuntu\r\n\r\n### Matplotlib Version\r\n\r\n3.5.2\r\n\r\n### Matplotlib Backend\r\n\r\nQtAgg\r\n\r\n### Python version\r\n\r\nPython 3.10.4\r\n\r\n### Jupyter version\r\n\r\nn/a\r\n\r\n### Installation\r\n\r\nconda\n"

#     test = """# reproducer.py
# import matplotlib.pyplot as plt
# from matplotlib import get_backend, rc_context

# def main():
#     # Uncommenting either of the lines below would work around the issue
#     # fig1 = plt.figure()
#     # plt.ion()
#     with rc_context():
#         fig2 = plt.figure()
#     before = f'{id(plt._pylab_helpers.Gcf)} {plt._pylab_helpers.Gcf.figs!r}'
#     get_backend()
#     after = f'{id(plt._pylab_helpers.Gcf)} {plt._pylab_helpers.Gcf.figs!r}'

#     assert before == after, '\n' + before + '\n' + after

# if __name__ == "__main__":
#     main()
# """

#     patch = """diff --git a/lib/matplotlib/__init__.py b/lib/matplotlib/__init__.py
# index c268a56724..b40f1246b9 100644
# --- a/lib/matplotlib/__init__.py
# +++ b/lib/matplotlib/__init__.py
# @@ -1087,7 +1087,9 @@ def rc_context(rc=None, fname=None):
#               plt.plot(x, y)  # uses 'print.rc'

#      \"\"\"
# +    from matplotlib._pylab_helpers import Gcf
#      orig = rcParams.copy()
# +    orig_figs = Gcf.figs.copy()  # Preserve the original figures
#      try:
#          if fname:
#              rc_file(fname)
# @@ -1096,6 +1098,7 @@ def rc_context(rc=None, fname=None):
#          yield
#      finally:
#          dict.update(rcParams, orig)  # Revert to the original rcs.
# +        Gcf.figs.update(orig_figs)  # Restore the original figures


#  def use(backend, *, force=True):"""

#     # run_with_retries(problem_stmt, test, patch)

#     success = False

#     for attempt_idx, (raw_response, thread, review_result) in enumerate(
#         run_with_retries(problem_stmt, test, patch), start=1
#     ):

#         success |= review_result is not None

#         # dump raw results for debugging
#         Path(f"agent_reviewer_raw_{attempt_idx}.json").write_text(
#             json.dumps(thread.to_msg(), indent=4)
#         )

#         if success:
#             print(f"Success at attempt {attempt_idx}. Review result is {review_result}")
#             break

#     if not success:
#         print("Still failing to produce valid review results after 5 attempts")
