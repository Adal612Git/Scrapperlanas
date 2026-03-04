import sys
from loguru import logger

# Remove default handlers
logger.remove()

# Rich console output with stage context and emojis
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[stage]: <20}</cyan> | <level>{message}</level>",
    colorize=True,
    level="DEBUG"
)

# File logging (more detailed)
logger.add(
    "runtime.log", 
    rotation="1 day", 
    retention="3 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[stage]: <20} | {message}",
    level="DEBUG"
)

# Helper to get logger with stage context
def get_logger(stage: str):
    """Get a logger bound to a specific stage for better tracking."""
    return logger.bind(stage=stage)
