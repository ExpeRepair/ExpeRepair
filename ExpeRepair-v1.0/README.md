# ExpeRepair-v1.0


## Introduction
ExpeRepair-v1.0 is a lightweight variant of ExpeRepair, featuring the following differences:
1. Memory Accumulation: ExpeRepair-v1.0 adopts a simpler way — it attempts to reproduce all issues, and if reproduction is successful (as determined by the LLM), it completes the entire issue resolution process. The repair trajectories from this process are then saved and organized in the Memory Module.
2. Memory Usage: ExpeRepair-v1.0 retrieves only demonstrations from the Memory Module, without utilizing summarized insights, and uses the top-k retrieved demonstrations to improve both reproduction script generation and patch generation.
3. Model Usage: ExpeRepair-v1.0 additionally employs o4-mini for the reasoning parts of reproduction, localization, and patch generation, and uses o4-mini instead of deepseek-r1 for patch validation.


## Trajectories
ExpeRepair records detailed trajectory files at each phase:

1. reproduction:
- conv_reproduce_X.json: the X-th attempt at generating a reproduction script.
- conv_reproduce_correct_X.json: LLM's determination of whether the X-th generated script is correct.
- conv_3_verified_test.json: in the validation stage, we additionally generate 3 test scripts for each issue.
- reproduce_experiences.jsonl: records the trajectories of the reproduction phase.

2. localization:
- search/search_round_file_[initial/refine].json: initially selects multiple suspicious files, then refines the selection to up to four files.
- search/search_round_edit_initial_X.json: identifies bug locations in the X-th selected file.
- search/search_round_edit_refine.json: combines all identified bug locations and selects the most likely ones.

3. generation:
- conv_patch_X.json: generates several candidate patches given the issue and retrieved demonstrations.
- conv_refine_patch_X.json: if none of the patches pass the reproduction script, refines them based on feedback (execution results).
- conv_expand_patch_X.json: if at least one patch passes, further improves the patch, since it may be a false positive due to incomplete tests.
- final_patch_X.json: saves all generated patches during the generation process.
- patch_experiences.jsonl: records the trajectories of the patch generation phase.

4. validation:
- select_patch_initial.json (optional): initially selects several patches based on their execution results on the reproduction script.
- select_patch_second.jsonl: ranks the selected patches based on their execution results on the 3 additional test scripts.
- ranked_final_patch.jsonl: final ranking of all generated patches. We submit only the top-ranked patch and submit only once.

