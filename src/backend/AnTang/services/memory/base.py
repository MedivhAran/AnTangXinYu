from abc import ABC, abstractmethod


class MemoryBase(ABC):
    """记忆存储的抽象基类，约定增删改查与历史接口。"""

    @abstractmethod
    def get(self, memory_id):
        """按 ID 读取单条记忆。

        Args:
            memory_id (str): 要读取的记忆 ID。

        Returns:
            dict: 读取到的记忆。
        """
        pass

    @abstractmethod
    def get_all(self):
        """列出全部记忆。

        Returns:
            list: 所有记忆组成的列表。
        """
        pass

    @abstractmethod
    def update(self, memory_id, data):
        """按 ID 更新一条记忆。

        Args:
            memory_id (str): 要更新的记忆 ID。
            data (str): 用于覆盖的新内容。

        Returns:
            dict: 表示更新成功的提示信息。
        """
        pass

    @abstractmethod
    def delete(self, memory_id):
        """按 ID 删除一条记忆。

        Args:
            memory_id (str): 要删除的记忆 ID。
        """
        pass

    @abstractmethod
    def history(self, memory_id):
        """获取某条记忆的变更历史。

        Args:
            memory_id (str): 要查询历史的记忆 ID。

        Returns:
            list: 该记忆的变更记录列表。
        """
        pass
