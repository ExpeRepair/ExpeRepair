import json
import logging
import os


def load_jsonl(filepath):
    """
    Load a JSONL file from the given filepath.

    Arguments:
    filepath -- the path to the JSONL file to load

    Returns:
    A list of dictionaries representing the data in each line of the JSONL file.
    """
    with open(filepath, "r") as file:
        return [json.loads(line) for line in file]


def write_jsonl(data, filepath):
    """
    Write data to a JSONL file at the given filepath.

    Arguments:
    data -- a list of dictionaries to write to the JSONL file
    filepath -- the path to the JSONL file to write
    """
    with open(filepath, "w") as file:
        for entry in data:
            file.write(json.dumps(entry) + "\n")


def load_json(filepath):
    return json.load(open(filepath, "r"))


def combine_by_instance_id(data):
    """
    Combine data entries by their instance ID.

    Arguments:
    data -- a list of dictionaries with instance IDs and other information

    Returns:
    A list of combined dictionaries by instance ID with all associated data.
    """
    combined_data = defaultdict(lambda: defaultdict(list))
    for item in data:
        instance_id = item.get("instance_id")
        if not instance_id:
            continue
        for key, value in item.items():
            if key != "instance_id":
                combined_data[instance_id][key].extend(
                    value if isinstance(value, list) else [value]
                )
    return [
        {**{"instance_id": iid}, **details} for iid, details in combined_data.items()
    ]


def setup_logger(log_file):
    logger = logging.getLogger(log_file)
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh.setFormatter(formatter)

    logger.addHandler(fh)
    return logger


def cleanup_logger(logger):
    handlers = logger.handlers[:]
    for handler in handlers:
        logger.removeHandler(handler)
        handler.close()


def load_existing_instance_ids(output_file):
    instance_ids = set()
    if os.path.exists(output_file):
        with open(output_file, "r") as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    instance_ids.add(data["instance_id"])
                except json.JSONDecodeError:
                    continue
    return instance_ids



def load_existing_reproduce_test(output_file):
    reproduce_data = {}
    with (open(output_file, "r") as f):
        for line in f:
            data = json.loads(line.strip())
            if data["test_content"] != '':
                reproduce_data[data["instance_id"]] = dict(test_content=data["test_content"],
                                                           reproduce_stdout=data["reproduce_stdout"],
                                                           reproduce_stderr=data["reproduce_stderr"],
                                                           returncode=data["returncode"],
                                                           reproduced=data["reproduced"],
                                                           )
            else:
                reproduce_data[data["instance_id"]] = None

    return reproduce_data


def load_existing_bug_locs(output_file):
    bug_data = {}
    with open(output_file, "r") as f:
        for line in f:
            data = json.loads(line.strip())
            bug_data[data["instance_id"]] = data['bug_locs']

    return bug_data

def load_existing_test_locs(output_file):
    bug_data = {}
    with open(output_file, "r") as f:
        for line in f:
            data = json.loads(line.strip())
            bug_data[data["instance_id"]] = data['test_locs']

    return bug_data


def load_existing_search_thread(output_file):
    def read_largest_patch_file(directory):
        import re
        largest_x = -1
        largest_file = None
        pattern = re.compile(r"search_round_edit_refine\.json")

        # 遍历目录，查找匹配的文件
        for filename in os.listdir(directory):
            match = pattern.match(filename)
            if match:
                # x = int(match.group(1))
                # if x > largest_x:
                #     largest_x = x
                largest_file = filename

        # 如果找到了最大 X 的文件，读取内容
        if largest_file:
            file_path = os.path.join(directory, largest_file)
            with open(file_path, 'r') as f:
                return json.load(f)

        # 如果没有找到任何匹配文件，返回 None
        return None

    return read_largest_patch_file(output_file)


def load_test_agent_meta(output_file):
    with open(output_file, "r") as f:
        meta_data = json.load(f)

    return meta_data


def show_project_structure(structure, spacing=0) -> str:
    """pprint the project structure"""

    pp_string = ""

    for key, value in structure.items():
        if "." in key and ".py" not in key:
            continue  # skip none python files
        if "." in key:
            pp_string += " " * spacing + str(key) + "\n"
        else:
            pp_string += " " * spacing + str(key) + "/" + "\n"
        if "classes" not in value:
            pp_string += show_project_structure(value, spacing + 4)

    return pp_string
