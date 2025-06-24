import os
import json

if __name__ == '__main__':
    repo_list = ['astropy', 'flask', 'pylint', 'pytest', 'requests', 'scikit-learn', 'seaborn', 'sphinx', 'xarray', 'django', 'sympy', 'matplotlib']
    generated_patch = {}
    for repo_name in repo_list:
    # repo_name = 'django'
        directory = f"results/validation_example/{repo_name}"


        for task_id in os.listdir(directory):
            # if task_id not in ['psf__requests-2148', 'pytest-dev__pytest-6116', 'matplotlib__matplotlib-23562', 'django__django-12470', 'scikit-learn__scikit-learn-13497', 'pylint-dev__pylint-7993', 'django__django-13448', 'matplotlib__matplotlib-23563', 'django__django-13551', 'scikit-learn__scikit-learn-14087', 'mwaskom__seaborn-3407', 'matplotlib__matplotlib-25332', 'django__django-12589', 'django__django-12747', 'matplotlib__matplotlib-24265', 'pytest-dev__pytest-5103', 'sympy__sympy-13915', 'sympy__sympy-14817', 'sympy__sympy-20049', 'django__django-13964', 'django__django-11797', 'sympy__sympy-21627', 'sympy__sympy-21612', 'pytest-dev__pytest-7220', 'django__django-14534', 'django__django-16229', 'django__django-12497', 'sympy__sympy-19487', 'django__django-11620', 'psf__requests-2674', 'sympy__sympy-12236', 'pytest-dev__pytest-8906', 'sympy__sympy-22840', 'django__django-12308', 'matplotlib__matplotlib-23987', 'mwaskom__seaborn-2848', 'django__django-12708', 'django__django-16400', 'pytest-dev__pytest-7168', 'sympy__sympy-16503', 'pytest-dev__pytest-5413']:
            #     continue
            with open(directory + '/' + task_id + '/ranked_final_patch.jsonl', 'r', encoding='utf-8') as f:
                data_lines = f.readlines()

            patch_list = [json.loads(data_line)['model_patch'] for data_line in data_lines]
            generated_patch[task_id] = patch_list


    print(len(generated_patch.keys()))

    for task_id in generated_patch.keys():
        patch_list = generated_patch[task_id]
        for num_idx in list(range(len(patch_list)))[:1]:
            with open(f'results/preds_example/final_{num_idx}.jsonl', 'a') as w:
                w.write(
                    json.dumps(
                        {
                            "model_name_or_path": f"ase_{num_idx}",
                            "instance_id": task_id,
                            "model_patch": patch_list[num_idx],
                        }
                    )
                    + "\n"
                )