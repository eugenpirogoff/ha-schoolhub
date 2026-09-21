from core.config import get_config

import datetime


def get_fetch_range(service_name):

    config = get_config()

    fetch_range = (
        config["settings"]
              [service_name]
              ["fetch_range"]
    )

    weeks_past = fetch_range["weeks_past"]
    weeks_future = fetch_range["weeks_future"]

    today = datetime.date.today()

    current_monday = (
        today -
        datetime.timedelta(
            days=today.weekday()
        )
    )

    start_date = (
        current_monday -
        datetime.timedelta(
            weeks=weeks_past
        )
    )

    end_date = (
        current_monday +
        datetime.timedelta(
            weeks=weeks_future + 1
        ) -
        datetime.timedelta(
            days=1
        )
    )

    return (
        start_date.isoformat(),
        end_date.isoformat()
    )