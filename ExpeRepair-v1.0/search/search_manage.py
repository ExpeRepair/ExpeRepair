import inspect
import json
import re
from collections.abc import Mapping
from os.path import join as pjoin
from pathlib import Path

from loguru import logger

from agents import agent_search
from data_structures import BugLocation
from log import print_acr
from search.search_backend import SearchBackend
from task import Task
from utils import parse_function_invocation


class SearchManager:
    def __init__(self, project_path: str, output_dir: str):
        # output dir for writing search-related things
        self.output_dir = pjoin(output_dir, "search")
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

        # record the search APIs being used, in each layer
        self.tool_call_layers: list[list[Mapping]] = []

        self.backend: SearchBackend = SearchBackend(project_path)

    def search_locations(
        self,
        task: Task,
        repo_struc,
        repro_result
    ):
        """
        Main entry point of the search manager.
        Returns:
            - Bug location info, which is a list of (code, intended behavior)
            - Class context code as string, or None if there is no context
            - The message thread that contains the search conversation.
        """

        ################################# relevant file initial ################################
        relevant_files, file_msg_thread = agent_search.locate_files_initial(task, repo_struc, repro_result)
        conversation_file = Path(self.output_dir, f"search_round_file_initial.json")
        # save current state before starting a new round
        file_msg_thread.save_to_file(conversation_file)

        file_api_calls = []
        for file_name in relevant_files:
            file_api_calls.append(f'get_file_skeleton("{file_name}")')

        collated_skeleton = ""
        for file_idx, api_call in enumerate(file_api_calls):
            func_name, func_args = parse_function_invocation(api_call)
            # TODO: there are currently duplicated code here and in agent_proxy.
            func_unwrapped = getattr(self.backend, func_name)
            while "__wrapped__" in func_unwrapped.__dict__:
                func_unwrapped = func_unwrapped.__wrapped__
            arg_spec = inspect.getfullargspec(func_unwrapped)
            arg_names = arg_spec.args[1:]  # first parameter is self

            assert len(func_args) == len(
                arg_names
            ), f"Number of argument is wrong in API call: {api_call}"

            kwargs = dict(zip(arg_names, func_args))

            function = getattr(self.backend, func_name)
            result_str, _, call_ok = function(**kwargs)
            if 'WOW$*&Could not find' not in result_str:
                collated_skeleton += f"### Skeleton {file_idx}:\n"
                collated_skeleton += result_str + "\n\n"


        ################################# relevant file refine ################################
        relevant_files, file_msg_thread = agent_search.locate_files_refine(task, collated_skeleton.strip(), repro_result)
        conversation_file = Path(self.output_dir, f"search_round_file_refine.json")
        # save current state before starting a new round
        file_msg_thread.save_to_file(conversation_file)

        collected_file_content = []
        for file_idx, file_name in enumerate(relevant_files):
            api_call = f'get_file_content_w_line("{file_name}")'
            func_name, func_args = parse_function_invocation(api_call)
            # TODO: there are currently duplicated code here and in agent_proxy.
            func_unwrapped = getattr(self.backend, func_name)
            while "__wrapped__" in func_unwrapped.__dict__:
                func_unwrapped = func_unwrapped.__wrapped__
            arg_spec = inspect.getfullargspec(func_unwrapped)
            arg_names = arg_spec.args[1:]  # first parameter is self

            assert len(func_args) == len(
                arg_names
            ), f"Number of argument is wrong in API call: {api_call}"

            kwargs = dict(zip(arg_names, func_args))

            function = getattr(self.backend, func_name)
            result_str, _, call_ok = function(**kwargs)
            # # TODO mfw
            # result_str = '\n'.join(result_str.split('\n')[:1800])
            if 'WOW$*&Could not find' not in result_str:
                # collated_skeleton = f"### File Content:\n"
                # collated_skeleton += result_str
                collected_file_content.append((file_name, result_str.strip()))

        ################################# relevant element ################################
        element_api_calls = []
        # {"class": "", "method": "", "variable": ""},
        for f_idx, (file_name, file_snippet) in enumerate(collected_file_content):
            relevant_elements, element_msg_thread = agent_search.locate_edits4file(task, file_snippet, repro_result)
            conversation_file = Path(self.output_dir, f"search_round_edit_initial_{f_idx}.json")
            # save current state before starting a new round
            element_msg_thread.save_to_file(conversation_file)

            for loc in relevant_elements:
                class_name = loc.get('class', "")
                method_name = loc.get('method', "")
                start_line = loc.get('start_line', "")
                end_line = loc.get('end_line', "")
                if start_line == 'None': start_line = ""
                if end_line == 'None': end_line = ""

                if start_line and end_line:
                    start_line, end_line = int(start_line), int(end_line)
                    central_line = (start_line + end_line) // 2
                    context_window = max(central_line - start_line, end_line - central_line) + 10
                    element_api_calls.append(f'get_code_around_line_v2("{file_name}", "{central_line}", "{context_window}")')

                elif class_name != "" and method_name == "":
                    element_api_calls.append(f'search_class_in_file("{class_name}", "{file_name}")')

                elif class_name != "" and method_name != "":
                    element_api_calls.append(f'search_method_in_class("{method_name}", "{class_name}")')

                elif class_name == "" and method_name != "":
                    element_api_calls.append(f'search_method_in_file("{method_name}", "{file_name}")')

                else:
                    print_acr(f"file: {file_name}\nclass: {class_name}\n"
                              f"method: {method_name}\nstart_line: {start_line}\nend_line: {end_line}",
                              "Wrong Related EDIT!")

        code_snippets = []
        for api_call in element_api_calls:
            func_name, func_args = parse_function_invocation(api_call)
            # TODO: there are currently duplicated code here and in agent_proxy.
            func_unwrapped = getattr(self.backend, func_name)
            while "__wrapped__" in func_unwrapped.__dict__:
                func_unwrapped = func_unwrapped.__wrapped__
            arg_spec = inspect.getfullargspec(func_unwrapped)
            arg_names = arg_spec.args[1:]  # first parameter is self

            assert len(func_args) == len(
                arg_names
            ), f"Number of argument is wrong in API call: {api_call}"

            kwargs = dict(zip(arg_names, func_args))

            function = getattr(self.backend, func_name)
            result_str, _, call_ok = function(**kwargs)
            code_snippets.extend(extract_code_snippets(result_str))


        collated_element = ""
        for element_idx, result_str in enumerate(code_snippets):
            if 'WOW$*&Could not find' not in result_str:
                collated_element += f"### Code Snippet {element_idx}:\n"
                collated_element += result_str.strip() + "\n\n"

        ################################# relevant edit ################################
        # {"class": "", "method": "", "start_line": "", "end_line": ""},
        relevant_edits, edit_msg_thread = agent_search.locate_edits4refine(task, collated_element.strip(), repro_result)
        conversation_file = Path(self.output_dir, f"search_round_edit_refine.json")
        # save current state before starting a new round
        edit_msg_thread.save_to_file(conversation_file)
        relevant_edits_list = [relevant_edits]
            # assert 1 == 2

        new_bug_locations_list = []
        for edit_idx, relevant_edits in enumerate(relevant_edits_list):
            new_bug_locations: list[BugLocation] = []
            for file_name in relevant_edits.keys():
                for loc in relevant_edits[file_name]:
                    class_name = loc.get('class', "")
                    method_name = loc.get('method', "")
                    start_line = loc.get('start_line', "")
                    end_line = loc.get('end_line', "")
                    if start_line == 'None': start_line = ""
                    if end_line == 'None': end_line = ""
                    relation_to_issue = loc.get("relation_to_issue", "")

                    if start_line and end_line:
                        start_line, end_line = int(start_line), int(end_line)
                        central_line = (start_line + end_line) // 2
                        context_window = max(central_line - start_line, end_line - central_line) + 10
                        api_call = f'get_code_around_line_v2("{file_name}", "{central_line}", "{context_window}")'

                    elif class_name != "" and method_name == "":
                        api_call = f'search_class_in_file("{class_name}", "{file_name}")'

                    elif class_name != "" and method_name != "":
                        api_call = f'search_method_in_class("{method_name}", "{class_name}")'

                    elif class_name == "" and method_name != "":
                        api_call = f'search_method_in_file("{method_name}", "{file_name}")'

                    else:
                        print_acr(f"file: {file_name}\nclass: {class_name}\n"
                                  f"method: {method_name}\nstart_line: {start_line}\nend_line: {end_line}",
                                  "Wrong Related EDIT!")
                        api_call = None

                    if api_call is None:
                        continue

                    func_name, func_args = parse_function_invocation(api_call)
                    # TODO: there are currently duplicated code here and in agent_proxy.
                    func_unwrapped = getattr(self.backend, func_name)
                    while "__wrapped__" in func_unwrapped.__dict__:
                        func_unwrapped = func_unwrapped.__wrapped__
                    arg_spec = inspect.getfullargspec(func_unwrapped)
                    arg_names = arg_spec.args[1:]  # first parameter is self

                    assert len(func_args) == len(
                        arg_names
                    ), f"Number of argument is wrong in API call: {api_call}"

                    kwargs = dict(zip(arg_names, func_args))

                    function = getattr(self.backend, func_name)
                    _, cur_res, _ = function(**kwargs)

                    for res in cur_res:
                        if res.start is None or res.end is None:
                            continue
                        new_bug_loc = BugLocation(res, self.backend.project_path, relation_to_issue)
                        new_bug_locations.append(new_bug_loc)

            # remove duplicates in the bug locations
            unique_bug_locations: list[BugLocation] = []
            seen_code = set()
            for loc in new_bug_locations:
                if loc.code not in seen_code:
                    seen_code.add(loc.code)
                    unique_bug_locations.append(loc)

            new_bug_locations = unique_bug_locations
            if new_bug_locations:
                # some locations can be extracted, good to proceed to patch gen
                bug_loc_file_processed = Path(
                    self.output_dir, f"bug_locations_after_process_{edit_idx}.json"
                )

                json_obj = [loc.to_dict() for loc in new_bug_locations]
                bug_loc_file_processed.write_text(json.dumps(json_obj, indent=4))

                logger.debug(
                    f"Bug location extracted successfully: {new_bug_locations}"
                )

                new_bug_locations_list.append(new_bug_locations)

        return new_bug_locations_list


    def start_new_tool_call_layer(self):
        self.tool_call_layers.append([])

    def add_tool_call_to_curr_layer(
        self, func_name: str, args: dict[str, str], result: bool
    ):
        self.tool_call_layers[-1].append(
            {
                "func_name": func_name,
                "arguments": args,
                "call_ok": result,
            }
        )

    def dump_tool_call_layers_to_file(self):
        """Dump the layers of tool calls to a file."""
        tool_call_file = Path(self.output_dir, "tool_call_layers.json")
        tool_call_file.write_text(json.dumps(self.tool_call_layers, indent=4))


def extract_code_snippets(text):
    """
    Extract all code snippets wrapped in triple backticks (```...```).

    Args:
        text (str): The input string containing code snippets.

    Returns:
        list: A list of code snippets extracted from the string.
    """
    pattern = r"```(.*?)```"  # Match text between triple backticks
    snippets = re.findall(pattern, text, re.DOTALL)  # Use re.DOTALL to match newlines
    return snippets
