"""Run the monitor and API: ``python -m ipmon`` (host/port via HOST, PORT env vars)."""
import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "ipmon.api:app_factory",
        factory=True,
        host=os.environ.get("HOST", "127.0.0.1"),  # internal-only by default (FR-29)
        port=int(os.environ.get("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
