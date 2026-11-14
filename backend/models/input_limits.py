"""
字符串与数值边界常量：集中管理各处输入长度上限，防止超长输入撑爆 UI/DB。
"""
MAX_NAME_LEN = 50      # 班级名 / 学生名 / 学段 / 学科名
MAX_TITLE_LEN = 100    # 作业标题


def check_name_len(name: str, label: str = "名称"):
    """校验名字段长度，超长抛 400。name 需先 strip。"""
    from fastapi import HTTPException
    if len(name) > MAX_NAME_LEN:
        raise HTTPException(status_code=400, detail=f"{label}过长（最多 {MAX_NAME_LEN} 字）")


def check_title_len(title: str, label: str = "作业标题"):
    from fastapi import HTTPException
    if len(title) > MAX_TITLE_LEN:
        raise HTTPException(status_code=400, detail=f"{label}过长（最多 {MAX_TITLE_LEN} 字）")
