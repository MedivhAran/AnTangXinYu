user_query_write = """
请将用户的原始查询改写为3条用于检索的查询，覆盖中英双语以同时召回中文与英文资料。

**改写要求**：
1. **前2条为中文**：语义与原查询一致，但换用不同词汇/句式，提高中文召回
2. **第3条为英文**：对原查询的忠实翻译，用于跨语言检索英文医学文献，必须保留专业术语（如 hypoglycemia、CGM、HbA1c、type 1 diabetes 等）
3. **语义保持**：严格保持原始查询的核心意图和关键信息不变
4. **自然流畅**：中英文都要自然、符合该语言习惯

**输出格式**：
必须返回标准JSON列表格式（恰好3个字符串）：['中文改写1', '中文改写2', 'English translation']

**用户原始查询**：{user_input}
"""


system_query_rewrite = """
{
  "instruction": "把用户 query 改写为3条检索查询：2条中文同义改写 + 1条忠实的英文翻译，用于中英文跨语言召回",
  "output_rules": {
    "format": "JSON",
    "structure": ["中文改写1", "中文改写2", "English translation"],
    "requirements": [
      "前两条中文需使用不同句式与用词，保持核心语义不变",
      "第三条为英文忠实翻译，保留医学专业术语，不做简化",
      "只输出一个含3个字符串的 JSON 列表，不要其他文字"
    ]
  },
  "example": {
    "input": {
      "query": "低血糖了应该怎么办"
    },
    "output": [
        "发生低血糖时应该如何处理？",
        "血糖过低有哪些应对和急救措施？",
        "What should I do when I experience hypoglycemia?"
      ]
  }
}
"""
