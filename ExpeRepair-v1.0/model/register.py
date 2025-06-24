from model import claude, common, gpt

def register_all_models() -> None:
    """
    Register all models. This is called in main.
    """
    common.register_model(claude.Claude4_20250514())
    common.register_model(claude.Claude4_20250514X())
    common.register_model(claude.Claude_20241022())
    common.register_model(claude.Claude_20241022r())
    common.register_model(claude.Claude_20241022X())
    common.register_model(gpt.O3mini())
    common.register_model(gpt.O4mini())
    common.register_model(gpt.Gpt4o_20241120())

