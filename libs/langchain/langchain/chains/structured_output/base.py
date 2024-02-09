import json
from typing import Any, Callable, Dict, Literal, Optional, Sequence, Type, Union

from langchain_core.output_parsers import (
    BaseGenerationOutputParser,
    BaseOutputParser,
    JsonOutputParser,
)
from langchain_core.prompts import BasePromptTemplate
from langchain_core.pydantic_v1 import BaseModel
from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import (
    convert_to_openai_function,
    convert_to_openai_tool,
)

from langchain.output_parsers import (
    JsonOutputKeyToolsParser,
    PydanticOutputParser,
    PydanticToolsParser,
)
from langchain.output_parsers.openai_functions import (
    JsonOutputFunctionsParser,
    PydanticAttrOutputFunctionsParser,
    PydanticOutputFunctionsParser,
)


def create_openai_fn_runnable(
    functions: Sequence[Union[Dict[str, Any], Type[BaseModel], Callable]],
    llm: Runnable,
    prompt: BasePromptTemplate,
    *,
    enforce_single_function_usage: bool = True,
    output_parser: Optional[Union[BaseOutputParser, BaseGenerationOutputParser]] = None,
    **kwargs: Any,
) -> Runnable:
    """Create a runnable sequence that uses OpenAI functions.

    Args:
        functions: A sequence of either dictionaries, pydantic.BaseModels classes, or
            Python functions. If dictionaries are passed in, they are assumed to
            already be a valid OpenAI functions. If only a single
            function is passed in, then it will be enforced that the model use that
            function. pydantic.BaseModels and Python functions should have docstrings
            describing what the function does. For best results, pydantic.BaseModels
            should have descriptions of the parameters and Python functions should have
            Google Python style args descriptions in the docstring. Additionally,
            Python functions should only use primitive types (str, int, float, bool) or
            pydantic.BaseModels for arguments.
        llm: Language model to use, assumed to support the OpenAI function-calling API.
        prompt: BasePromptTemplate to pass to the model.
        enforce_single_function_usage: only used if a single function is passed in. If
            True, then the model will be forced to use the given function. If False,
            then the model will be given the option to use the given function or not.
        output_parser: BaseLLMOutputParser to use for parsing model outputs. By default
            will be inferred from the function types. If pydantic.BaseModels are passed
            in, then the OutputParser will try to parse outputs using those. Otherwise
            model outputs will simply be parsed as JSON. If multiple functions are
            passed in and they are not pydantic.BaseModels, the chain output will
            include both the name of the function that was returned and the arguments
            to pass to the function.

    Returns:
        A runnable sequence that will pass in the given functions to the model when run.

    Example:
        .. code-block:: python

                from typing import Optional

                from langchain.chains.structured_output import create_openai_fn_runnable
                from langchain_openai import ChatOpenAI
                from langchain_core.prompts import ChatPromptTemplate
                from langchain_core.pydantic_v1 import BaseModel, Field


                class RecordPerson(BaseModel):
                    '''Record some identifying information about a person.'''

                    name: str = Field(..., description="The person's name")
                    age: int = Field(..., description="The person's age")
                    fav_food: Optional[str] = Field(None, description="The person's favorite food")


                class RecordDog(BaseModel):
                    '''Record some identifying information about a dog.'''

                    name: str = Field(..., description="The dog's name")
                    color: str = Field(..., description="The dog's color")
                    fav_food: Optional[str] = Field(None, description="The dog's favorite food")


                llm = ChatOpenAI(model="gpt-4", temperature=0)
                prompt = ChatPromptTemplate.from_messages(
                    [
                        ("system", "You are a world class algorithm for recording entities."),
                        ("human", "Make calls to the relevant function to record the entities in the following input: {input}"),
                        ("human", "Tip: Make sure to answer in the correct format"),
                    ]
                )
                chain = create_openai_fn_runnable([RecordPerson, RecordDog], llm, prompt)
                chain.invoke({"input": "Harry was a chubby brown beagle who loved chicken"})
                # -> RecordDog(name="Harry", color="brown", fav_food="chicken")
    """  # noqa: E501
    if not functions:
        raise ValueError("Need to pass in at least one function. Received zero.")
    openai_functions = [convert_to_openai_function(f) for f in functions]
    llm_kwargs: Dict[str, Any] = {"functions": openai_functions, **kwargs}
    if len(openai_functions) == 1 and enforce_single_function_usage:
        llm_kwargs["function_call"] = {"name": openai_functions[0]["name"]}
    output_parser = output_parser or get_openai_output_parser(functions)
    return prompt | llm.bind(**llm_kwargs) | output_parser


def create_structured_output_runnable(
    output_schema: Union[Dict[str, Any], Type[BaseModel]],
    llm: Runnable,
    prompt: BasePromptTemplate,
    *,
    output_parser: Optional[Union[BaseOutputParser, BaseGenerationOutputParser]] = None,
    enforce_function_usage: bool = True,
    return_single: bool = True,
    mode: Literal[
        "openai-functions", "openai-tools", "openai-json"
    ] = "openai-functions",
    **kwargs: Any,
) -> Runnable:
    """Create a runnable for extracting structured outputs.

    Args:
        output_schema: Either a dictionary or pydantic.BaseModel class. If a dictionary
            is passed in, it's assumed to already be a valid JsonSchema.
            For best results, pydantic.BaseModels should have docstrings describing what
            the schema represents and descriptions for the parameters.
        llm: Language model to use. Assumed to support the OpenAI function-calling API 
            if mode is 'openai-function'. Assumed to support OpenAI response_format 
            parameter if mode is 'openai-json'.
        prompt: BasePromptTemplate to pass to the model. If mode is 'openai-json' and 
            prompt has input variable 'output_schema' then the given output_schema 
            will be converted to a JsonSchema and inserted in the prompt.
        output_parser: Output parser to use for parsing model outputs. By default
            will be inferred from the function types. If pydantic.BaseModel is passed
            in, then the OutputParser will try to parse outputs using the pydantic 
            class. Otherwise model outputs will be parsed as JSON.
        mode: How structured outputs are extracted from the model. If 'openai-functions' 
            then OpenAI function calling is used with the deprecated 'functions', 
            'function_call' schema. If 'openai-tools' then OpenAI function 
            calling with the latest 'tools', 'tool_choice' schema is used. This is 
            recommended over 'openai-functions'. If 'openai-json' then OpenAI model 
            with response_format set to JSON is used.
        enforce_function_usage: Only applies when mode is 'openai-tools' or 
            'openai-functions'. If True, then the model will be forced to use the given 
            output schema. If False, then the model can elect whether to use the output 
            schema.
        return_single: Only applies when mode is 'openai-tools'. Whether to a list of 
            structured outputs or a single one. If True and model does not return any 
            structured outputs then chain output is None. If False and model does not 
            return any structured outputs then chain output is an empty list.
        **kwargs: Additional named arguments.

    Returns:
        A runnable sequence that will return a structured output(s) matching the given 
            output_schema.
    
    OpenAI tools example with Pydantic schema (mode='openai-tools'):
        .. code-block:: python
        
                from typing import Optional

                from langchain.chains import create_structured_output_runnable
                from langchain_openai import ChatOpenAI
                from langchain_core.pydantic_v1 import BaseModel, Field


                class RecordDog(BaseModel):
                    '''Record some identifying information about a dog.'''

                    name: str = Field(..., description="The dog's name")
                    color: str = Field(..., description="The dog's color")
                    fav_food: Optional[str] = Field(None, description="The dog's favorite food")

                llm = ChatOpenAI(model="gpt-3.5-turbo-0125", temperature=0)
                structured_llm = create_structured_output_runnable(
                    RecordDog, 
                    llm, 
                    mode="openai-tools", 
                    enforce_function_usage=True, 
                    return_single=True
                )
                structured_llm.invoke("Harry was a chubby brown beagle who loved chicken")
                # -> RecordDog(name="Harry", color="brown", fav_food="chicken")
                
    OpenAI tools example with dict schema (mode="openai-tools"):
        .. code-block:: python
        
                from typing import Optional

                from langchain.chains import create_structured_output_runnable
                from langchain_openai import ChatOpenAI


                dog_schema = {
                    "type": "function",
                    "function": {
                        "name": "record_dog",
                        "description": "Record some identifying information about a dog.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "description": "The dog's name",
                                    "type": "string"
                                },
                                "color": {
                                    "description": "The dog's color",
                                    "type": "string"
                                },
                                "fav_food": {
                                    "description": "The dog's favorite food",
                                    "type": "string"
                                }
                            },
                            "required": ["name", "color"]
                        }
                    }
                }


                llm = ChatOpenAI(model="gpt-3.5-turbo-0125", temperature=0)
                structured_llm = create_structured_output_runnable(
                    doc_schema, 
                    llm, 
                    mode="openai-tools", 
                    enforce_function_usage=True, 
                    return_single=True
                )
                structured_llm.invoke("Harry was a chubby brown beagle who loved chicken")
                # -> {'name': 'Harry', 'color': 'brown', 'fav_food': 'chicken'}
    
    OpenAI functions example (mode="openai-functions"):
        .. code-block:: python

                from typing import Optional

                from langchain.chains.structured_output import create_structured_output_runnable
                from langchain_openai import ChatOpenAI
                from langchain_core.prompts import ChatPromptTemplate
                from langchain_core.pydantic_v1 import BaseModel, Field

                class Dog(BaseModel):
                    '''Identifying information about a dog.'''

                    name: str = Field(..., description="The dog's name")
                    color: str = Field(..., description="The dog's color")
                    fav_food: Optional[str] = Field(None, description="The dog's favorite food")

                llm = ChatOpenAI(model="gpt-3.5-turbo-0125", temperature=0)
                prompt = ChatPromptTemplate.from_messages(
                    [
                        ("system", "You are a world class algorithm for extracting information in structured formats."),
                        ("human", "Use the given format to extract information from the following input: {input}"),
                        ("human", "Tip: Make sure to answer in the correct format"),
                    ]
                )
                chain = create_structured_output_runnable(Dog, llm, prompt, mode="openai-functions")
                chain.invoke({"input": "Harry was a chubby brown beagle who loved chicken"})
                # -> Dog(name="Harry", color="brown", fav_food="chicken")
                
    OpenAI json response format example (mode="openai-json"):
        .. code-block:: python
        
                from typing import Optional

                from langchain.chains.structured_output import create_structured_output_runnable
                from langchain_openai import ChatOpenAI
                from langchain_core.prompts import ChatPromptTemplate
                from langchain_core.pydantic_v1 import BaseModel, Field

                class Dog(BaseModel):
                    '''Identifying information about a dog.'''

                    name: str = Field(..., description="The dog's name")
                    color: str = Field(..., description="The dog's color")
                    fav_food: Optional[str] = Field(None, description="The dog's favorite food")

                llm = ChatOpenAI(model="gpt-3.5-turbo-0125", temperature=0)
                system = '''You are a world class assistant for extracting information in structured JSON formats. \
                
                Extract a valid JSON blob from the user input that matches the following JSON Schema:
                
                {output_schema}'''
                prompt = ChatPromptTemplate.from_messages(
                    [
                        ("system", system),
                        ("human", "{input}"),
                    ]
                )
                chain = create_structured_output_runnable(Dog, llm, prompt, mode="openai-json")
                chain.invoke({"input": "Harry was a chubby brown beagle who loved chicken"})
    """  # noqa: E501
    if mode == "openai-tools":
        return _create_openai_tools_runnable(
            output_schema,
            llm,
            prompt=prompt,
            output_parser=output_parser,
            enforce_tool_usage=enforce_function_usage,
            return_single=return_single,
        )

    elif mode == "openai-functions":
        # for backwards compatibility
        enforce_single_function_usage = kwargs.get(
            "enforce_single_function_usage", enforce_function_usage
        )

        return _create_openai_functions_structured_output_runnable(
            output_schema,
            llm,
            prompt,
            output_parser=output_parser,
            enforce_single_function_usage=enforce_single_function_usage,
            **kwargs,
        )
    elif mode == "openai-json":
        return _create_openai_json_runnable(
            output_schema, llm, prompt, output_parser=output_parser, **kwargs
        )
    else:
        raise ValueError(
            f"Invalid mode {mode}. Expected one of 'openai-tools', 'openai-functions', "
            f"'openai-json'."
        )


def _create_openai_tools_runnable(
    tool: Union[Dict[str, Any], Type[BaseModel], Callable],
    llm: Runnable,
    *,
    prompt: Optional[BasePromptTemplate],
    output_parser: Optional[Union[BaseOutputParser, BaseGenerationOutputParser]],
    enforce_tool_usage: bool,
    return_single: bool,
) -> Runnable:
    oai_tool = convert_to_openai_tool(tool)
    llm_kwargs: Dict[str, Any] = {"tools": [oai_tool]}
    if enforce_tool_usage:
        llm_kwargs["tool_choice"] = {
            "type": "function",
            "function": {"name": oai_tool["function"]["name"]},
        }
    output_parser = output_parser or _get_openai_tool_output_parser(
        tool, return_single=return_single
    )
    if prompt:
        return prompt | llm.bind(**llm_kwargs) | output_parser
    else:
        return llm.bind(**llm_kwargs) | output_parser


def _get_openai_tool_output_parser(
    tool: Union[Dict[str, Any], Type[BaseModel], Callable], return_single: bool = False
) -> Union[BaseOutputParser, BaseGenerationOutputParser]:
    if isinstance(tool, type) and issubclass(tool, BaseModel):
        output_parser: Union[
            BaseOutputParser, BaseGenerationOutputParser
        ] = PydanticToolsParser(tools=[tool], return_single=return_single)
    else:
        key_name = convert_to_openai_tool(tool)["function"]["name"]
        output_parser = JsonOutputKeyToolsParser(
            return_single=return_single, key_name=key_name
        )
    return output_parser


def get_openai_output_parser(
    functions: Sequence[Union[Dict[str, Any], Type[BaseModel], Callable]],
) -> Union[BaseOutputParser, BaseGenerationOutputParser]:
    """Get the appropriate function output parser given the user functions.

    Args:
        functions: Sequence where element is a dictionary, a pydantic.BaseModel class,
            or a Python function. If a dictionary is passed in, it is assumed to
            already be a valid OpenAI function.

    Returns:
        A PydanticOutputFunctionsParser if functions are Pydantic classes, otherwise
            a JsonOutputFunctionsParser. If there's only one function and it is
            not a Pydantic class, then the output parser will automatically extract
            only the function arguments and not the function name.
    """
    if isinstance(functions[0], type) and issubclass(functions[0], BaseModel):
        if len(functions) > 1:
            pydantic_schema: Union[Dict, Type[BaseModel]] = {
                convert_to_openai_function(fn)["name"]: fn for fn in functions
            }
        else:
            pydantic_schema = functions[0]
        output_parser: Union[
            BaseOutputParser, BaseGenerationOutputParser
        ] = PydanticOutputFunctionsParser(pydantic_schema=pydantic_schema)
    else:
        output_parser = JsonOutputFunctionsParser(args_only=len(functions) <= 1)
    return output_parser


def _create_openai_json_runnable(
    output_schema: Union[Dict[str, Any], Type[BaseModel]],
    llm: Runnable,
    prompt: BasePromptTemplate,
    *,
    output_parser: Optional[Union[BaseOutputParser, BaseGenerationOutputParser]] = None,
) -> Runnable:
    """"""
    if isinstance(output_schema, type) and issubclass(output_schema, BaseModel):
        output_parser = output_parser or PydanticOutputParser(
            pydantic_object=output_schema,
        )
        schema_as_dict = convert_to_openai_function(output_schema)["parameters"]
    else:
        output_parser = output_parser or JsonOutputParser()
        schema_as_dict = output_schema

    if "output_schema" in prompt.input_variables:
        prompt = prompt.partial(output_schema=json.dumps(schema_as_dict, indent=2))

    llm = llm.bind(response_format={"type": "json_object"})
    return prompt | llm | output_parser


def _create_openai_functions_structured_output_runnable(
    output_schema: Union[Dict[str, Any], Type[BaseModel]],
    llm: Runnable,
    prompt: BasePromptTemplate,
    *,
    output_parser: Optional[Union[BaseOutputParser, BaseGenerationOutputParser]] = None,
    **kwargs: Any,
) -> Runnable:
    if isinstance(output_schema, dict):
        function: Any = {
            "name": "output_formatter",
            "description": (
                "Output formatter. Should always be used to format your response to the"
                " user."
            ),
            "parameters": output_schema,
        }
    else:

        class _OutputFormatter(BaseModel):
            """Output formatter. Should always be used to format your response to the user."""  # noqa: E501

            output: output_schema  # type: ignore

        function = _OutputFormatter
        output_parser = output_parser or PydanticAttrOutputFunctionsParser(
            pydantic_schema=_OutputFormatter, attr_name="output"
        )
    return create_openai_fn_runnable(
        [function],
        llm,
        prompt,
        output_parser=output_parser,
        **kwargs,
    )
