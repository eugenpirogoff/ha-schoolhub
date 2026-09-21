import yaml

from core.paths import CONFIG_FILE


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_config():
    return load_config()


def get_account(account_name):
    config = get_config()
    account = config["accounts"][account_name]

    return {
        "username": str(account["username"]),
        "password": str(account["password"])
    }


def get_child(child_name):
    config = get_config()
    return config["children"][child_name]


def get_children():
    config = get_config()
    return list(config["children"].keys())