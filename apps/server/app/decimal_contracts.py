from typing import Annotated

from pydantic import Field, StrictStr

DecimalText = Annotated[
    StrictStr, Field(pattern=r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?$")
]
