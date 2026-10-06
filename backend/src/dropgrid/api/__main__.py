import uvicorn

from dropgrid.config import Settings


def main() -> None:
    config = Settings()
    uvicorn.run(
        "dropgrid.api.app:create_app",
        factory=True,
        host=config.backend_host,
        port=config.backend_port,
    )


if __name__ == "__main__":
    main()
