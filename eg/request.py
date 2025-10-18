from dataclasses import dataclass


@dataclass
class Request:
    # all fields must be json serializable
    path: str
    gamma: float
