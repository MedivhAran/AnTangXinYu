from abc import ABC, abstractmethod


class VectorStoreBase(ABC):
    """向量库的抽象基类，约定集合管理与向量增删改查接口。"""

    @abstractmethod
    def create_col(self, name, vector_size, distance):
        """创建一个新集合。"""
        pass

    @abstractmethod
    def insert(self, vectors, payloads=None, ids=None):
        """向集合插入向量。"""
        pass

    @abstractmethod
    def search(self, query, vectors, limit=5, filters=None):
        """检索相似向量。"""
        pass

    @abstractmethod
    def delete(self, vector_id):
        """按 ID 删除一个向量。"""
        pass

    @abstractmethod
    def update(self, vector_id, vector=None, payload=None):
        """更新一个向量及其 payload。"""
        pass

    @abstractmethod
    def get(self, vector_id):
        """按 ID 读取一个向量。"""
        pass

    @abstractmethod
    def list_cols(self):
        """列出全部集合。"""
        pass

    @abstractmethod
    def delete_col(self):
        """删除集合。"""
        pass

    @abstractmethod
    def col_info(self):
        """获取集合的信息。"""
        pass

    @abstractmethod
    def list(self, filters=None, limit=None):
        """列出全部记忆。"""
        pass

    @abstractmethod
    def reset(self):
        """通过删除并重建集合来重置。"""
        pass
