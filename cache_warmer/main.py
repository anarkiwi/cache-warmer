import logging
import os

from cache_warmer import acng, config, proxpi, registry, scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
)

logger = logging.getLogger(__name__)


def main() -> None:  # pragma: no cover
    config_path = os.environ.get("CONFIG_FILE", "/config/config.yaml")
    cfg = config.load(config_path)

    while True:
        run_at = scheduler.next_run_time(
            cfg.schedule.timezone,
            cfg.schedule.window_start,
            cfg.schedule.window_end,
        )
        logger.info("next run at %s", run_at)
        scheduler.sleep_until(run_at)

        logger.info("starting acng warm")
        acng.run(cfg)
        logger.info("acng done")

        logger.info("starting proxpi warm")
        proxpi.run(cfg)
        logger.info("proxpi done")

        if cfg.docker_registry:
            logger.info("starting docker registry warm")
            registry.run(cfg)
            logger.info("docker registry done")


if __name__ == "__main__":
    main()
