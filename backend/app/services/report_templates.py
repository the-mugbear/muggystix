class ReportTemplates:
    """The stylesheet of the systemic executive briefing
    (``ReportGenerator.generate_systemic_executive_html``) — the one HTML
    document the host exports still produce.

    The page generators that lived here rendered the test plan execution
    report, which went with plans in v2.442.0; the host HTML report's own
    rules (nav, sortable tables, dossiers) and its scripts went with that
    report.  Only what the briefing's markup uses is kept."""

    @staticmethod
    def get_css_styles() -> str:
        """The briefing's ``<style>`` block."""
        return """
        <style>
            :root {
                color-scheme: dark;
                --bg: #0d1117;
                --bg-elevated: #141a22;
                --bg-panel: #19212b;
                --bg-panel-soft: #202a36;
                --text: #edf2f7;
                --muted: #a6b3c2;
                --subtle: #7d8a99;
                --border: #2d3847;
                --accent: #21c7a8;
                --accent-strong: #4fd1c5;
                --accent-warm: #f5b84b;
                --danger: #ff6b7a;
                --warning: #f7c948;
                --success: #38c172;
                --info: #63b3ed;
                --shadow: 0 18px 50px rgba(0, 0, 0, 0.32);
            }

            * {
                box-sizing: border-box;
            }

            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                line-height: 1.6;
                margin: 0;
                padding: 28px;
                background:
                    radial-gradient(circle at top left, rgba(33, 199, 168, 0.16), transparent 34rem),
                    linear-gradient(135deg, #0d1117 0%, #121822 50%, #16151c 100%);
                color: var(--text);
            }

            a {
                color: var(--accent-strong);
            }

            .report-header {
                background:
                    linear-gradient(135deg, rgba(33, 199, 168, 0.22) 0%, rgba(245, 184, 75, 0.10) 100%),
                    var(--bg-elevated);
                color: var(--text);
                padding: 32px;
                border-radius: 8px;
                margin-bottom: 22px;
                box-shadow: var(--shadow);
                border: 1px solid rgba(79, 209, 197, 0.26);
            }

            .report-title {
                font-size: clamp(2rem, 5vw, 3.1rem);
                font-weight: 700;
                margin-bottom: 10px;
                line-height: 1.05;
            }

            .report-subtitle {
                font-size: 1.2em;
                color: var(--muted);
                margin-bottom: 0;
            }

            /* The briefing's "nothing to show here" and lead-in lines. */
            .muted {
                color: var(--muted);
            }

            .section {
                background: var(--bg-panel);
                margin-bottom: 22px;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: var(--shadow);
                border: 1px solid var(--border);
            }

            .section-header {
                background: linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02));
                padding: 20px;
                border-bottom: 1px solid var(--border);
                font-size: 1.4em;
                font-weight: 600;
                color: var(--text);
            }

            .section-content {
                padding: 25px;
            }

            .stats-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }

            .stat-card {
                background: linear-gradient(135deg, rgba(33, 199, 168, 0.12), rgba(255,255,255,0.03));
                border: 1px solid var(--border);
                border-radius: 8px;
                padding: 20px;
                text-align: center;
                transition: transform 0.2s ease;
            }

            .stat-card:hover {
                transform: translateY(-2px);
                box-shadow: 0 14px 30px rgba(0,0,0,0.28);
                border-color: rgba(33, 199, 168, 0.55);
            }

            .stat-value {
                font-size: 2.2em;
                font-weight: 700;
                color: var(--accent-strong);
                margin-bottom: 5px;
            }

            .stat-label {
                color: var(--muted);
                font-size: 0.9em;
                text-transform: uppercase;
                font-weight: 600;
                letter-spacing: 0;
            }

            table {
                width: 100%;
                border-collapse: collapse;
                margin: 20px 0;
                background: var(--bg-panel);
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 10px 28px rgba(0,0,0,0.20);
                border: 1px solid var(--border);
            }

            th {
                background: linear-gradient(135deg, #202a36 0%, #263241 100%);
                color: var(--text);
                font-weight: 600;
                padding: 15px 12px;
                text-align: left;
                font-size: 0.9em;
                text-transform: uppercase;
                letter-spacing: 0;
                border-bottom: 1px solid var(--border);
            }

            td {
                padding: 12px;
                border-bottom: 1px solid var(--border);
                vertical-align: top;
            }

            tr:nth-child(even) {
                background-color: rgba(255,255,255,0.05);
            }

            tr:hover {
                background-color: rgba(33, 199, 168, 0.08);
                transition: background-color 0.2s ease;
            }

            .metadata {
                display: flex;
                justify-content: space-between;
                align-items: center;
                flex-wrap: wrap;
                gap: 10px;
                font-size: 0.9em;
                opacity: 0.9;
            }

            .version-tag {
                font-size: 0.85em;
                color: var(--muted);
                margin-top: 6px;
                letter-spacing: 0;
            }

            @media print {
                :root {
                    color-scheme: light;
                    --bg: #ffffff;
                    --bg-elevated: #ffffff;
                    --bg-panel: #ffffff;
                    --bg-panel-soft: #f4f6f8;
                    --text: #1f2933;
                    --muted: #52606d;
                    --border: #d9e2ec;
                }
                body { background: white; color: var(--text); padding: 12px; }
                .section { page-break-inside: avoid; box-shadow: none; }
                .report-header { background: #ffffff !important; color: var(--text); box-shadow: none; }
                .stat-card:hover { transform: none; }
                tr:hover { background-color: transparent; }
            }

            @media (max-width: 768px) {
                .stats-grid { grid-template-columns: 1fr; }
                .metadata { flex-direction: column; align-items: flex-start; }
            }
        </style>
        """
