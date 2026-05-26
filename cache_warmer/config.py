import dataclasses

import yaml


@dataclasses.dataclass
class ScheduleConfig:
    timezone: str
    window_start: str
    window_end: str


@dataclasses.dataclass
class AcngConfig:
    url: str
    matrix: list
    extras_dir: str


@dataclasses.dataclass
class ProxpiConfig:
    url: str
    cache_dir: str
    workers: int


@dataclasses.dataclass
class Config:
    schedule: ScheduleConfig
    apt_cacher_ng: AcngConfig
    proxpi: ProxpiConfig


def load(path: str) -> Config:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    sched = data["schedule"]
    acng = data["apt_cacher_ng"]
    prox = data["proxpi"]
    return Config(
        schedule=ScheduleConfig(
            timezone=sched["timezone"],
            window_start=sched["window_start"],
            window_end=sched["window_end"],
        ),
        apt_cacher_ng=AcngConfig(
            url=acng["url"],
            matrix=acng.get("matrix", []),
            extras_dir=acng.get("extras_dir", "/config/acng-extras"),
        ),
        proxpi=ProxpiConfig(
            url=prox["url"],
            cache_dir=prox["cache_dir"],
            workers=prox.get("workers", 8),
        ),
    )
