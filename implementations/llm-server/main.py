import os
import strawberry
from RequestState import RequestState
from llm.LLMService import LLMService
from schema import Prompt, Response
from config.AppConfig import AppConfig
from fastapi import FastAPI
from strawberry.fastapi import GraphQLRouter


@strawberry.type
class Query:
    @strawberry.field
    def prompt(self, prompt: Prompt) -> Response:
        """
        Main entrypoint for handling LLM prompt requests via GraphQL.

        :param prompt: User input wrapped in Prompt type.
        :return: Processed LLM output wrapped in Response type.
        """
        request_state = RequestState(prompt.prompt_type)  # Track request state
        llm_service = LLMService(prompt, request_state)  # Instantiate service to handle process
        return llm_service.process_prompt()  # Execute full LLM pipeline

schema = strawberry.Schema(query=Query)
graphql_app = GraphQLRouter(schema)
app = FastAPI()  # ✅ This is the ASGI app that uvicorn expects
app.include_router(graphql_app, prefix="/graphql")  # Now available at /graphql

