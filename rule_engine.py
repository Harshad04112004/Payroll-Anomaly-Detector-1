import ast
import io
import json
from datetime import datetime
from pathlib import Path
import pandas as pd

RULES_FILE = Path("rules.json")
HISTORY_FILE = Path("run_history.json")

REQUIRED_COLUMNS = {
    "employee_id": "str",
    "employee_name": "str",
    "department": "str",
    "status": "str",
    "bank_account": "str",
    "basic_pay": "num",
    "allowances": "num",
    "hours_worked": "num",
    "overtime_hours": "num",
    "overtime_pay": "num",
    "gross_pay": "num",
    "pf_deduction": "num",
    "tax_other_deductions": "num",
    "leave_balance": "num",
    "net_pay": "num",
}

ALLOWED_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "len": len,
}

ALLOWED_AST_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.USub,
    ast.UAdd,
)


def load_rules() -> list[dict]:
    if not RULES_FILE.exists():
        return []
    with open(RULES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_rules(rules: list[dict]) -> None:
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2)


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def append_history(entry: dict) -> None:
    history = load_history()
    history.insert(0, entry)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[:50], f, indent=2)


def validate_payroll_file(uploaded_file) -> tuple[pd.DataFrame | None, list[str]]:
    errors = []
    try:
        if uploaded_file.name.lower().endswith(".csv"):
            df = pd.read_csv(uploaded_file, dtype={"bank_account": str, "employee_id": str})
        elif uploaded_file.name.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(uploaded_file, dtype={"bank_account": str, "employee_id": str})
        else:
            return None, ["Unsupported file format. Upload a .csv or .xlsx file."]
    except Exception as exc:
        return None, [f"Failed to read file: {exc}"]

    if df.empty:
        return None, ["The uploaded file is completely empty."]

    # 1. Check missing columns
    missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_cols:
        errors.append(f"Missing required column(s): {', '.join(missing_cols)}")
        return None, errors

    # 2. Check completely blank rows
    blank_rows = df[df[list(REQUIRED_COLUMNS.keys())].isna().all(axis=1)]
    if not blank_rows.empty:
        row_nums = [int(idx) + 2 for idx in blank_rows.index]
        errors.append(f"Found {len(blank_rows)} completely blank row(s) at spreadsheet row(s): {row_nums[:10]}")

    # 3. Check missing required cell values and numeric data types
    for col, expected_type in REQUIRED_COLUMNS.items():
        null_indices = df[df[col].isna()].index.tolist()
        if null_indices:
            row_nums = [int(i) + 2 for i in null_indices[:8]]
            errors.append(f"Column '{col}' has {len(null_indices)} blank cell(s) (e.g., row {row_nums}).")

        if expected_type == "num":
            coerced = pd.to_numeric(df[col], errors="coerce")
            bad_type_mask = coerced.isna() & df[col].notna()
            if bad_type_mask.any():
                bad_rows = [int(i) + 2 for i in df[bad_type_mask].index[:8]]
                bad_vals = df.loc[bad_type_mask, col].astype(str).tolist()[:5]
                errors.append(
                    f"Column '{col}' expects numeric values, found invalid text {bad_vals} at row(s) {bad_rows}."
                )
            else:
                df[col] = coerced

    if errors:
        return None, errors

    df["employee_id"] = df["employee_id"].astype(str).str.strip()
    df["employee_name"] = df["employee_name"].astype(str).str.strip()
    df["department"] = df["department"].astype(str).str.strip()
    df["status"] = df["status"].astype(str).str.strip()
    df["bank_account"] = df["bank_account"].astype(str).str.strip()

    return df, []


def safe_eval_expr(expr: str, context: dict):
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_AST_NODES):
            raise ValueError(f"Disallowed syntax in expression: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
                raise ValueError(f"Function '{getattr(node.func, 'id', 'unknown')}' is not permitted.")
    code = compile(tree, "<rule_expr>", "eval")
    return eval(code, {"__builtins__": {}}, {**ALLOWED_FUNCTIONS, **context})


def evaluate_single_rule(df: pd.DataFrame, rule: dict) -> list[dict]:
    working_df = df.copy()
    working_df["is_duplicate_bank_account"] = working_df.duplicated(subset=["bank_account"], keep=False)

    flags = []
    fields = rule.get("fields", [])
    condition = rule["condition"]
    expected_formula = rule.get("expected_formula", "")

    for idx, row in working_df.iterrows():
        ctx = row.to_dict()
        ctx["row_number"] = int(idx) + 2
        try:
            is_broken = bool(safe_eval_expr(condition, ctx))
        except Exception as exc:
            raise ValueError(f"Rule '{rule['name']}' failed on row {idx + 2}: {exc}")

        if is_broken:
            if fields:
                actual_str = ", ".join(f"{f}={row[f]}" for f in fields if f in row)
            else:
                actual_str = "Condition matched"

            expected_val = expected_formula
            if expected_formula:
                try:
                    computed = safe_eval_expr(expected_formula, ctx)
                    expected_val = str(computed)
                except Exception:
                    expected_val = str(expected_formula)

            flags.append(
                {
                    "Sheet Row": int(idx) + 2,
                    "Employee ID": row["employee_id"],
                    "Employee Name": row["employee_name"],
                    "Department": row["department"],
                    "Rule ID": rule["id"],
                    "Rule Broken": rule["name"],
                    "Severity": rule["severity"],
                    "Actual Value": actual_str,
                    "Expected Value": expected_val,
                    "Reason / Action": rule["message"],
                }
            )
    return flags


def run_rule_engine(df: pd.DataFrame, rules: list[dict]) -> tuple[pd.DataFrame, dict]:
    all_flags = []
    active_rules = [r for r in rules if r.get("enabled", True)]

    for rule in active_rules:
        all_flags.extend(evaluate_single_rule(df, rule))

    flags_df = pd.DataFrame(all_flags)
    total_records = len(df)

    if flags_df.empty:
        summary = {
            "total_records": total_records,
            "clean_records": total_records,
            "flagged_records": 0,
            "total_exceptions": 0,
            "flags_by_rule": {},
            "most_common_error": "None",
        }
        return flags_df, summary

    severity_rank = {"Critical": 1, "High": 2, "Medium": 3, "Low": 4}
    flags_df["_sev_rank"] = flags_df["Severity"].map(severity_rank).fillna(99)
    flags_df = flags_df.sort_values(by=["_sev_rank", "Sheet Row"]).drop(columns=["_sev_rank"]).reset_index(drop=True)

    flagged_emp_count = flags_df["Employee ID"].nunique()
    clean_records = total_records - flagged_emp_count
    rule_counts = flags_df["Rule Broken"].value_counts().to_dict()
    most_common_error = next(iter(rule_counts.keys())) if rule_counts else "None"

    summary = {
        "total_records": total_records,
        "clean_records": clean_records,
        "flagged_records": flagged_emp_count,
        "total_exceptions": len(flags_df),
        "flags_by_rule": rule_counts,
        "most_common_error": f"{most_common_error} ({rule_counts[most_common_error]} flags)",
    }
    return flags_df, summary


def export_exceptions_to_excel(flags_df: pd.DataFrame, summary: dict, filename: str) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        flags_df.to_excel(writer, sheet_name="Flagged_Exceptions", index=False)
        summary_rows = [
            {"Metric": "Source File", "Value": filename},
            {"Metric": "Generated Timestamp", "Value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
            {"Metric": "Total Employee Records", "Value": summary["total_records"]},
            {"Metric": "Clean Records", "Value": summary["clean_records"]},
            {"Metric": "Flagged Employees", "Value": summary["flagged_records"]},
            {"Metric": "Total Rule Exceptions", "Value": summary["total_exceptions"]},
            {"Metric": "Most Common Error", "Value": summary["most_common_error"]},
        ]
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Run_Summary", index=False)

        ws = writer.sheets["Flagged_Exceptions"]
        from openpyxl.styles import Font, PatternFill

        header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        sev_fills = {
            "Critical": PatternFill(start_color="FECACA", end_color="FECACA", fill_type="solid"),
            "High": PatternFill(start_color="FED7AA", end_color="FED7AA", fill_type="solid"),
            "Medium": PatternFill(start_color="FEF08A", end_color="FEF08A", fill_type="solid"),
            "Low": PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid"),
        }

        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font

        sev_col_idx = list(flags_df.columns).index("Severity") + 1
        for row_idx in range(2, len(flags_df) + 2):
            sev_val = ws.cell(row=row_idx, column=sev_col_idx).value
            if sev_val in sev_fills:
                for col_idx in range(1, len(flags_df.columns) + 1):
                    ws.cell(row=row_idx, column=col_idx).fill = sev_fills[sev_val]

        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 48)

    return output.getvalue()