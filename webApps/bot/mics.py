from aiogram.fsm.storage.base import (
    BaseStorage, KeyBuilder, StateType, StorageKey
)
from aiogram.fsm.state import State
from aiogram.types import TelegramObject

from typing import Any, Callable, Dict, Optional
from pathlib import Path
import json
import asyncpg

from exceptions.exceptions import RegistrationError, StructureError
from database.models.user import User
from core.container import Container

path = Path(__file__).parent

_JsonLoads = Callable[..., Any]
_JsonDumps = Callable[..., str]


class PostgreSQLKeyBuilder(KeyBuilder):
    def build(self, key: StorageKey) -> str:
        return f"/{key.chat_id}/{key.user_id}/"


class PostgreSQLStorage(BaseStorage):
    """
    PostgreSQLStorage required :code:`asyncpg` package

    :code:`pip install asyncpg`
    """

    def __init__(
        self,
        postgresql_url: str,
        key_builder: Optional[KeyBuilder] = PostgreSQLKeyBuilder(),
        json_loads: _JsonLoads = json.loads,
        json_dumps: _JsonDumps = json.dumps
    ) -> None:
        """
        Initialize PostgreSQLStorage.

        Parameters:
            postgresql_url (str): The connection URL for the PostgreSQL database.
                                  The URL should be in the following format:
                                  postgresql://username:password@host:port/database
            key_builder (Optional[KeyBuilder]): Optional custom key builder.
            json_loads (_JsonLoads): Function for loading JSON data.
            json_dumps (_JsonDumps): Function for dumping JSON data.
        """
        self.postgresql_url = postgresql_url
        self.key_builder = key_builder

        self.postgresql_connection = None

        self.json_loads = json_loads
        self.json_dumps = json_dumps

    async def get_connection(self):
        if self.postgresql_connection:
            try:
                await self.postgresql_connection.execute('SELECT 1')
            except (asyncpg.exceptions.PostgresError, asyncpg.exceptions.InterfaceError):
                await self.close()
                self.postgresql_connection = None

        if not self.postgresql_connection:
            self.postgresql_connection = await asyncpg.connect(
                self.postgresql_url
            )

    async def ensure_exists(self, key: str) -> None:
        memory_cache_model = {"key": "TEXT", "state": "TEXT", "data": "JSON"}
        
        memory_cache = await self.postgresql_connection.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'memory_cache';"
        )

        if not memory_cache:
            await self.postgresql_connection.execute(
                "CREATE TABLE memory_cache (key TEXT PRIMARY KEY, state TEXT, data JSON);"
            )
        else:
            exists = {model_field['column_name']: model_field['data_type'].upper() for model_field in memory_cache}
            for model_field, type in memory_cache_model.items():
                if model_field not in exists or exists[model_field] != type:
                    raise StructureError(
                        f"Exists memory_cache with different structure."
                    )
        
        result = await self.postgresql_connection.fetchval(
            "SELECT 1 FROM memory_cache WHERE key = $1;", key)
        if not result:
            query = "INSERT INTO memory_cache (key, state, data) VALUES ($1, '', '{}');"
            await self.postgresql_connection.execute(query, key)

    async def close(self) -> None:
        await self.postgresql_connection.close()

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        await self.get_connection()

        postgres_key = self.key_builder.build(key)
        await self.ensure_exists(postgres_key)


        query = (
            "INSERT INTO memory_cache (key, state, data) VALUES ($1, $2, '{}') "
            "ON CONFLICT (key) DO UPDATE SET state = EXCLUDED.state;"
        )
        await self.postgresql_connection.execute(query, postgres_key, state.state if isinstance(state, State) else state)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        await self.get_connection()

        postgres_key = self.key_builder.build(key)
        await self.ensure_exists(postgres_key)

        result = await self.postgresql_connection.fetchval(
            "SELECT state FROM memory_cache WHERE key = $1;", postgres_key
        )
        return result if result else None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        await self.get_connection()

        postgres_key = self.key_builder.build(key)
        await self.ensure_exists(postgres_key)

        query = (
            "INSERT INTO memory_cache (key, state, data) VALUES ($1, '', $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data;"
        )
        await self.postgresql_connection.execute(query, postgres_key, self.json_dumps(data))

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        await self.get_connection()

        postgres_key = self.key_builder.build(key)
        await self.ensure_exists(postgres_key)


        result = await self.postgresql_connection.fetchval(
            "SELECT data FROM memory_cache WHERE key = $1;", postgres_key)
        if result:
            return self.json_loads(result)
        return {}


async def registr(
    event_from_user: TelegramObject, user: User, container: Container
) -> None:
    if (user):
        if not (user.first_name is event_from_user.first_name):
            await container.user_repository.update_first_name(
                event_from_user.id, event_from_user.first_name
            )
        if not (user.last_name is event_from_user.last_name):
            await container.user_repository.update_last_name(
                event_from_user.id, event_from_user.last_name
            )
        if not (user.username is event_from_user.username):
            await container.user_repository.update_username(
                event_from_user.id, event_from_user.username
            )
    else:
        registr = await container.user_repository.entry(
            event_from_user.id, event_from_user.first_name, event_from_user.last_name, event_from_user.username
        )

        if not registr:
            raise RegistrationError