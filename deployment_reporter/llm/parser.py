from ..domain.models import DeploymentReport


def parse_report(text):
    value = text.strip()
    if value.startswith("```json") and value.endswith("```"):
        value = value[7:-3].strip()
    elif value.startswith("```") and value.endswith("```"):
        value = value[3:-3].strip()
    return DeploymentReport.model_validate_json(value)
