from __future__ import annotations

from typing import Protocol

from infrastructure.persistence.dto.tensor_dto import EdgeTensorDTO


class TensorRepository(Protocol):
    def store_tensor(self, tensor: EdgeTensorDTO) -> None: ...
    def get_tensor_series(self, experiment_id: str) -> list[EdgeTensorDTO]: ...
    def replay(self) -> list[EdgeTensorDTO]: ...
