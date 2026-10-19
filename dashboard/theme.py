"""Workspace styling for Streamlit's native navigation, controls and evidence views."""

from html import escape
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).parent / "assets"


def apply_theme() -> None:
    st.logo(str(ASSETS / "wordmark.svg"), size="large", icon_image=str(ASSETS / "wordmark.svg"))
    st.html("""
    <style>
    :root {--workspace-line:#DFE6DF;--workspace-muted:#728078;--workspace-ink:#26382F;}
    html, body, [data-testid="stApp"] {letter-spacing:0;}
    [data-testid="stAppViewContainer"] {background:#F6F8F6;}
    [data-testid="stHeader"] {background:transparent;}
    [data-testid="stMainBlockContainer"] {max-width:1600px;padding:2.5rem 2.8rem 3.5rem;}
    [data-testid="stSidebar"] {min-width:246px;max-width:246px;border-right:1px solid var(--workspace-line);}
    [data-testid="stSidebarHeader"] {padding:1.35rem 1.5rem 1rem;height:auto;min-height:94px;}
    [data-testid="stSidebarLogo"] {width:180px;height:47px;max-width:100%;object-fit:contain;}
    [data-testid="stSidebarNav"] {padding:0.35rem 0.85rem 0;}
    [data-testid="stSidebarNav"] ul {gap:4px;}
    [data-testid="stSidebarNav"] li {padding:0;}
    [data-testid="stSidebarNav"] a {min-height:43px;border-radius:6px;padding:10px 13px;}
    [data-testid="stSidebarNav"] a[aria-current="page"] {background:#E5EDE3;color:#356B54;font-weight:600;}
    [data-testid="stSidebarNav"] a:hover {background:#EEF3EC;}
    [data-testid="stSidebarNav"] [data-testid="stSidebarNavSeparator"] {margin:1rem 0;}
    [data-testid="stSidebarUserContent"] {padding:1rem 1.5rem;}
    [data-testid="stSidebarUserContent"] p {font-size:12px;color:#728078;}
    h1,h2,h3,h4,h5,h6 {letter-spacing:0!important;overflow-wrap:anywhere;}
    h1 {padding-top:0!important;line-height:1.25!important;}
    h2,h3 {padding-top:0.3rem!important;}
    p {line-height:1.6;}
    [data-testid="stCaptionContainer"] {color:var(--workspace-muted);}
    [data-testid="stMetric"] {background:#FFF;border:1px solid var(--workspace-line);border-radius:7px;
        padding:1.15rem 1.25rem;min-height:118px;box-sizing:border-box;}
    [data-testid="stMetricLabel"] {color:#708075;font-size:13px;white-space:normal;}
    [data-testid="stMetricValue"] {font-size:29px;font-weight:600;line-height:1.25;}
    [data-testid="stMetricValue"] > div {overflow-wrap:anywhere;white-space:normal;}
    [data-testid="stVerticalBlockBorderWrapper"] > div {border-color:var(--workspace-line);border-radius:7px;}
    [data-testid="stDataFrame"] {border:1px solid var(--workspace-line);border-radius:6px;background:#FFF;}
    [data-testid="stSelectbox"] > div > div, [data-testid="stMultiSelect"] > div > div,
    [data-testid="stTextInput"] > div > div {background:#FFF;}
    [data-testid="stWidgetLabel"] p {font-size:12px;color:#63756A;}
    [data-testid="stAlert"] {border-radius:6px;padding:0.7rem 1rem;}
    [data-testid="stAlert"] p {font-size:13px;}
    [data-testid="stExpander"] {background:#FFF;border-radius:6px;}
    [data-testid="stButton"] button, [data-testid="stLinkButton"] a {min-height:39px;font-size:13px;}
    [data-testid="stTabs"] [role="tablist"] {border-bottom:1px solid var(--workspace-line);gap:1.2rem;}
    hr {border-color:var(--workspace-line);margin:1.1rem 0;}
    .workspace-topline {display:flex;align-items:center;justify-content:space-between;gap:18px;
        border-bottom:1px solid var(--workspace-line);padding:0 0 1rem;margin-bottom:1.4rem;font-size:11px;color:#79877D;}
    .workspace-topline .context {display:flex;gap:14px;align-items:center;}
    .workspace-topline .model {text-align:right;color:#667A6C;overflow-wrap:anywhere;}
    .workspace-kicker {font-size:11px;color:#718677;margin-bottom:8px;}
    .runtime-strip {display:flex;flex-wrap:wrap;gap:10px 22px;align-items:center;border-top:1px solid var(--workspace-line);
        border-bottom:1px solid var(--workspace-line);padding:12px 0;margin:0.1rem 0 1rem;font-size:12px;}
    .runtime-item {display:flex;align-items:center;gap:7px;color:#76867B;}
    .runtime-item strong {font-weight:500;color:#405C4B;}
    .status-dot {display:inline-block;width:7px;height:7px;border-radius:50%;flex:0 0 7px;background:#9CA7A0;}
    .status-dot.good {background:#5C947C;}.status-dot.warn {background:#BD9951;}.status-dot.bad {background:#C47C70;}
    .status-dot.neutral {background:#8F9CA5;}
    .graph-legend {display:flex;flex-wrap:wrap;gap:16px;font-size:11px;color:#7A877E;margin:2px 0 10px;}
    .graph-legend span {display:flex;align-items:center;gap:6px;}
    .health-row {display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 0;
        border-bottom:1px solid #EEF1EC;font-size:12px;}
    .health-row:last-child {border-bottom:0;}
    .health-name {color:#455B4E;overflow-wrap:anywhere;min-width:0;}
    .health-value {display:flex;align-items:center;gap:7px;font-size:10px;color:#7E8C82;white-space:nowrap;}
    .health-value.good {color:#4E8368;}.health-value.bad {color:#AC645B;}
    .health-meter {height:6px;border-radius:3px;background:#EBEFEB;display:flex;overflow:hidden;margin:10px 0 16px;}
    .health-meter .good {background:#639B83;}.health-meter .bad {background:#C77C6C;}.health-meter .neutral {background:#C4CEC7;}
    .empty-evidence {padding:24px 0;color:#7F8D83;font-size:13px;border-top:1px solid #E8EDE6;}
    .workspace-footer {font-size:11px;color:#91A094;border-top:1px solid var(--workspace-line);padding-top:1rem;margin-top:1.5rem;}
    @media (max-width:1050px) {
        [data-testid="stMainBlockContainer"] {padding:2.5rem 1.5rem 2rem;}
    }
    @media (max-width:640px) {
        [data-testid="stMainBlockContainer"] {padding:3.6rem 1rem 2rem;}
        .workspace-topline {align-items:flex-start;gap:10px;font-size:10px;}
        .workspace-topline .context {gap:7px;}.workspace-topline .model {max-width:150px;}
        .runtime-strip {gap:9px 16px;}.runtime-item {font-size:11px;}
        [data-testid="stMetric"] {min-height:100px;padding:0.9rem 1rem;}
        [data-testid="stMetricValue"] {font-size:27px;}
        .st-key-overview_metrics [data-testid="stHorizontalBlock"] {flex-wrap:wrap!important;gap:0.7rem!important;}
        .st-key-overview_metrics [data-testid="stColumn"] {
            flex:1 1 calc(50% - 0.7rem)!important;min-width:calc(50% - 0.7rem)!important;
        }
    }
    </style>
    """)


def page_header(title: str, section: str, subtitle: str, provider: str, model: str) -> None:
    st.html(f'<div class="workspace-topline"><div class="context"><span>WORKSPACE</span>'
            f'<span>/</span><span>{escape(section.upper())}</span></div>'
            f'<span class="model">{escape(provider.title())} &nbsp; / &nbsp; {escape(model)}</span></div>')
    left, right = st.columns([0.76, 0.24], vertical_alignment="center")
    with left:
        st.html('<div class="workspace-kicker">CLOUD OPERATIONS &amp; RESEARCH</div>')
        st.title(title)
        st.caption(subtitle)
    with right:
        st.link_button("Open storefront", "http://localhost:8080", icon=":material/open_in_new:",
                       use_container_width=True)


def runtime_strip(items: list[tuple[str, str, str]]) -> None:
    parts = [f'<span class="runtime-item"><i class="status-dot {tone}"></i>'
             f'{escape(label)} <strong>{escape(value)}</strong></span>' for label, value, tone in items]
    st.html('<div class="runtime-strip" role="status">' + ''.join(parts) + '</div>')


def graph_legend(*, skills: bool = False) -> None:
    values = [("good", "Low risk"), ("warn", "Medium risk"), ("neutral", "Service")] if skills else [
        ("good", "Healthy"), ("bad", "Observed fault"), ("warn", "Potential exposure"), ("neutral", "Unknown / stale")]
    st.html('<div class="graph-legend">' + ''.join(
        f'<span><i class="status-dot {tone}"></i>{label}</span>' for tone, label in values) + '</div>')
