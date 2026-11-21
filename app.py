from datetime import datetime
import pandas as pd
import streamlit as st
from rule_engine import (
    REQUIRED_COLUMNS,
    append_history,
    evaluate_single_rule,
    export_exceptions_to_excel,
    load_history,
    load_rules,
    run_rule_engine,
    save_rules,
    validate_payroll_file,
)

st.set_page_config(page_title="IMS Nucleii |  Payroll Anomaly Detector", page_icon="🛡️", layout="wide")

st.markdown(
    """
    <style>
    .stApp { background-color: #0f172a; color: #f8fafc; }
    div[data-testid="stMetric"] {
        background: rgba(30, 41, 59, 0.85);
        border: 1px solid rgba(148, 163, 184, 0.25);
        padding: 14px 18px;
        border-radius: 10px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if "rules" not in st.session_state:
    st.session_state.rules = load_rules()
if "df" not in st.session_state:
    st.session_state.df = None
if "uploaded_filename" not in st.session_state:
    st.session_state.uploaded_filename = None
if "last_logged_signature" not in st.session_state:
    st.session_state.last_logged_signature = None

st.title("🛡️ Payroll Anomaly Detector")
st.caption("IMS Nucleii • Rules-as-Data Pre-Disbursement Audit Engine")

# Sidebar Upload & Schema Validator
with st.sidebar:
    st.header("1. Upload Payroll Sheet")
    uploaded = st.file_uploader("Upload Monthly Payroll (.csv or .xlsx)", type=["csv", "xlsx", "xls"])

    if uploaded is not None:
        df_parsed, validation_errors = validate_payroll_file(uploaded)
        if validation_errors:
            st.session_state.df = None
            st.error("File Validation Failed:")
            for err in validation_errors:
                st.warning(f"• {err}")
        else:
            st.session_state.df = df_parsed
            st.session_state.uploaded_filename = uploaded.name
            st.success(f"Validated {len(df_parsed)} rows from `{uploaded.name}`")

    st.divider()
    st.markdown("**Expected Columns:**")
    st.code(", ".join(REQUIRED_COLUMNS.keys()), language="text")

if st.session_state.df is None:
    st.info("Upload `sample_payroll_300.xlsx` or `sample_payroll_300.csv` from the sidebar to run the rule engine.")
    st.stop()

df = st.session_state.df
rules = st.session_state.rules

# Execute active rules live on current dataframe
try:
    flags_df, summary = run_rule_engine(df, rules)
except ValueError as err:
    st.error(f"Rule Execution Error: {err}")
    st.stop()

# Log run in history whenever file or active rule set changes
run_sig = f"{st.session_state.uploaded_filename}:{summary['total_exceptions']}:{sum(1 for r in rules if r['enabled'])}"
if st.session_state.last_logged_signature != run_sig:
    append_history(
        {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "file_name": st.session_state.uploaded_filename,
            "total_records": summary["total_records"],
            "clean_records": summary["clean_records"],
            "flagged_employees": summary["flagged_records"],
            "total_exceptions": summary["total_exceptions"],
            "active_rules": sum(1 for r in rules if r["enabled"]),
            "most_common_error": summary["most_common_error"],
        }
    )
    st.session_state.last_logged_signature = run_sig

# Top KPI Summary Strip
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total Records", summary["total_records"])
k2.metric("Clean Records", summary["clean_records"])
k3.metric("Flagged Employees", summary["flagged_records"])
k4.metric("Total Rule Flags", summary["total_exceptions"])
k5.metric("Active Rules", f"{sum(1 for r in rules if r['enabled'])} / {len(rules)}")

st.markdown(f"**Most Common Error:** `{summary['most_common_error']}`")

tab_report, tab_editor, tab_test, tab_history = st.tabs(
    ["📋 Anomaly Exception Report", "⚙️ Live Rule Editor", "🧪 Rule Test Sandbox", "🕒 Run History"]
)

# TAB 1: ANOMALY REPORT
with tab_report:
    if flags_df.empty:
        st.success("Zero anomalies detected! All rows passed active payroll rules.")
    else:
        c_chart, c_filters = st.columns([1.2, 1.8])
        with c_chart:
            st.subheader("Flags by Rule")
            rule_breakdown = pd.DataFrame(
                list(summary["flags_by_rule"].items()), columns=["Rule Broken", "Flag Count"]
            )
            st.bar_chart(rule_breakdown.set_index("Rule Broken"), height=220)

        with c_filters:
            st.subheader("Filter & Sort Exceptions")
            f1, f2, f3 = st.columns(3)
            sev_filter = f1.multiselect(
                "Severity",
                options=["Critical", "High", "Medium", "Low"],
                default=["Critical", "High", "Medium", "Low"],
            )
            rule_filter = f2.multiselect(
                "Rule Broken",
                options=sorted(flags_df["Rule Broken"].unique().tolist()),
                default=sorted(flags_df["Rule Broken"].unique().tolist()),
            )
            dept_filter = f3.multiselect(
                "Department",
                options=sorted(flags_df["Department"].unique().tolist()),
                default=sorted(flags_df["Department"].unique().tolist()),
            )

            sort_col = st.selectbox(
                "Sort By",
                options=["Severity (Default)", "Sheet Row", "Employee ID", "Department", "Rule Broken"],
            )

        filtered_df = flags_df[
            (flags_df["Severity"].isin(sev_filter))
            & (flags_df["Rule Broken"].isin(rule_filter))
            & (flags_df["Department"].isin(dept_filter))
        ].copy()

        if sort_col != "Severity (Default)":
            filtered_df = filtered_df.sort_values(by=sort_col)

        def style_severity(row):
            colors = {
                "Critical": "background-color: rgba(239, 68, 68, 0.28); color: #fecaca;",
                "High": "background-color: rgba(249, 115, 22, 0.25); color: #fed7aa;",
                "Medium": "background-color: rgba(234, 179, 8, 0.22); color: #fef08a;",
                "Low": "background-color: rgba(59, 130, 246, 0.22); color: #bfdbfe;",
            }
            style = colors.get(row["Severity"], "")
            return [style] * len(row)

        st.dataframe(
            filtered_df.style.apply(style_severity, axis=1),
            use_container_width=True,
            hide_index=True,
            height=420,
        )

        excel_bytes = export_exceptions_to_excel(
            filtered_df, summary, st.session_state.uploaded_filename
        )
        st.download_button(
            label="📥 Export Flagged Exceptions to Excel (.xlsx)",
            data=excel_bytes,
            file_name=f"payroll_exceptions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )

# TAB 2: LIVE RULE EDITOR (ADD / EDIT / TOGGLE / DELETE WITHOUT CODE)
with tab_editor:
    st.subheader("Manage Payroll Rules (Stored in `rules.json`)")
    st.write("Toggle rules ON/OFF, edit thresholds, or delete rules — changes immediately re-run on the uploaded sheet.")

    updated_rules = []
    for i, rule in enumerate(rules):
        badge = "🟢" if rule["enabled"] else "⚪"
        with st.expander(f"{badge} [{rule['id']}] {rule['name']} — Severity: {rule['severity']}", expanded=False):
            col_a, col_b, col_c = st.columns([2, 1, 1])
            new_name = col_a.text_input("Rule Name", value=rule["name"], key=f"name_{rule['id']}")
            new_sev = col_b.selectbox(
                "Severity",
                ["Critical", "High", "Medium", "Low"],
                index=["Critical", "High", "Medium", "Low"].index(rule["severity"]),
                key=f"sev_{rule['id']}",
            )
            new_enabled = col_c.toggle("Enabled", value=rule["enabled"], key=f"en_{rule['id']}")

            new_cond = st.text_input(
                "Python Boolean Condition (triggers exception when True)",
                value=rule["condition"],
                key=f"cond_{rule['id']}",
            )
            c_exp, c_fld = st.columns(2)
            new_exp = c_exp.text_input(
                "Expected Value / Formula",
                value=rule.get("expected_formula", ""),
                key=f"exp_{rule['id']}",
            )
            new_fields = c_fld.text_input(
                "Inspected Columns (comma-separated)",
                value=", ".join(rule.get("fields", [])),
                key=f"fld_{rule['id']}",
            )
            new_msg = st.text_input("Exception Reason Message", value=rule["message"], key=f"msg_{rule['id']}")

            delete_rule = st.checkbox("Delete this rule", key=f"del_{rule['id']}")
            if not delete_rule:
                updated_rules.append(
                    {
                        "id": rule["id"],
                        "name": new_name.strip(),
                        "category": rule.get("category", "Custom"),
                        "fields": [x.strip() for x in new_fields.split(",") if x.strip()],
                        "condition": new_cond.strip(),
                        "expected_formula": new_exp.strip(),
                        "severity": new_sev,
                        "message": new_msg.strip(),
                        "enabled": new_enabled,
                    }
                )

    if st.button("💾 Save All Rule Changes & Re-Run Payroll Audit", type="primary"):
        st.session_state.rules = updated_rules
        save_rules(updated_rules)
        st.success("Rules saved to `rules.json` and re-evaluated live!")
        st.rerun()

# TAB 3: RULE TEST SANDBOX (PREVIEW BEFORE SAVING)
with tab_test:
    st.subheader("🧪 Test a New Rule Before Saving")
    st.write("Preview which rows of the current payroll file would be flagged before committing the rule to `rules.json`.")

    with st.form("test_rule_form"):
        t1, t2 = st.columns([2, 1])
        test_name = t1.text_input("New Rule Name", placeholder="e.g., High Overtime in Admin Dept")
        test_sev = t2.selectbox("Severity", ["Critical", "High", "Medium", "Low"], index=1)

        test_cond = st.text_input(
            "Condition Expression (e.g., department == 'Admin' and overtime_hours > 20)",
            placeholder="overtime_pay > (basic_pay * 0.5)",
        )
        t3, t4 = st.columns(2)
        test_exp = t3.text_input("Expected Value / Formula", placeholder="round(basic_pay * 0.5, 2)")
        test_fields = t4.text_input("Fields to Display (comma-separated)", placeholder="department, overtime_hours, basic_pay")
        test_msg = st.text_input("Exception Message", placeholder="Overtime payout exceeds 50% of basic pay.")

        c_btn1, c_btn2 = st.columns(2)
        preview_clicked = c_btn1.form_submit_button("🔍 Preview Flagged Rows (Dry Run)")
        save_new_clicked = c_btn2.form_submit_button("✅ Save & Activate Rule Live", type="primary")

    if preview_clicked or save_new_clicked:
        if not test_name.strip() or not test_cond.strip():
            st.warning("Enter both a Rule Name and a Condition Expression.")
        else:
            next_num = len(rules) + 1
            candidate_rule = {
                "id": f"RULE-{next_num:02d}",
                "name": test_name.strip(),
                "category": "Custom",
                "fields": [x.strip() for x in test_fields.split(",") if x.strip()],
                "condition": test_cond.strip(),
                "expected_formula": test_exp.strip(),
                "severity": test_sev,
                "message": test_msg.strip() or f"Failed rule: {test_name.strip()}",
                "enabled": True,
            }
            try:
                preview_flags = evaluate_single_rule(df, candidate_rule)
                st.info(f"Dry-Run Result: This rule flags **{len(preview_flags)}** row(s) out of {len(df)}.")
                if preview_flags:
                    st.dataframe(pd.DataFrame(preview_flags), use_container_width=True, hide_index=True)

                if save_new_clicked:
                    st.session_state.rules.append(candidate_rule)
                    save_rules(st.session_state.rules)
                    st.success(f"Saved `{candidate_rule['id']}: {candidate_rule['name']}` and updated live report!")
                    st.rerun()
            except Exception as exc:
                st.error(f"Syntax / Evaluation Error in Rule: {exc}")

# TAB 4: RUN HISTORY
with tab_history:
    st.subheader("🕒 Audit Run History")
    history = load_history()
    if not history:
        st.write("No runs logged yet.")
    else:
        st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)