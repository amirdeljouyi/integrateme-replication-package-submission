import os
import argparse
import uvicorn

# ----------- CLI Handling for Different Properties ----------- #
def main():

    parser = argparse.ArgumentParser(description="Run Strawberry GraphQL LLM API with dynamic model selection.")
    parser.add_argument("--model", type=str, help="LLM model to use (default: gpt-4o-mini)", default="gpt-4o-mini")
    parser.add_argument("--write_responses", type=bool, help="Write the responses into a file", default=False)
    parser.add_argument("--write_failed_responses", type=bool, help="Write the failed responses into a file", default=False)
    parser.add_argument("--log_to_file", type=bool, help="Write the logs", default=False)
    parser.add_argument("--log_level", type=str, help="Log Level", default="INFO")
    parser.add_argument("--host", type=str, help="Host for API server (default: 0.0.0.0)", default="0.0.0.0")
    parser.add_argument("--port", type=int, help="Port for API server (default: 8000)", default=8000)
    parser.add_argument("--attempt", type=int, help="Attempt (for naming)")
    parser.add_argument("--signature", type=str, help="signature (for naming)")
    args = parser.parse_args()

    os.environ["MODEL_NAME"] = args.model
    os.environ["WRITE_RESPONSES"] = str(args.write_responses)
    os.environ["WRITE_FAILED_RESPONSES"] = str(args.write_failed_responses)
    os.environ["LOG_TO_FILE"] = str(args.log_to_file)
    os.environ["LOG_LEVEL"] = str(args.log_level)
    os.environ["ATTEMPT"] = str(args.attempt)
    os.environ["SIGNATURE"] = str(args.signature)

    from config.AppConfig import AppConfig
    AppConfig.init_once()

    logger = AppConfig.get_instance().setup_logger("Main")
    logger.info(f"\n🚀 Starting server with model: {args.model}\n")

    from main import app
    uvicorn.run(app, host=args.host, port=args.port, reload=False)

if __name__ == "__main__":
    main()