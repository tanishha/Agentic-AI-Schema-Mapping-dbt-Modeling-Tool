import os
from openai import OpenAI


def get_llm_client() -> OpenAI:
    provider = os.getenv("LLM_PROVIDER", "ollama")
    if provider == "azure":
        return OpenAI(
            base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
        )
    return OpenAI(
        base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.getenv("LLM_API_KEY", "ollama"),
    )


def get_model_name(fast: bool = False) -> str:
    provider = os.getenv("LLM_PROVIDER", "ollama")
    if provider == "azure":
        var = "AZURE_OPENAI_DEPLOYMENT_FAST" if fast else "AZURE_OPENAI_DEPLOYMENT"
    else:
        var = "LLM_MODEL_FAST" if fast else "LLM_MODEL"

    value = os.getenv(var)
    if not value:
        raise RuntimeError(f"Missing required env var: {var}")
    return value