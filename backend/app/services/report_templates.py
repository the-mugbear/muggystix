class ReportTemplates:
    """The stylesheet and the table scripts the host report embeds.

    The page generators that lived here rendered the test plan execution
    report, which went with plans in v2.442.0."""
    
    @staticmethod
    def get_css_styles() -> str:
        """Professional CSS styling for HTML reports"""
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

            html {
                /* Anchor-link jumps don't land under the sticky .report-nav */
                scroll-padding-top: 5rem;
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

            .report-nav {
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin: 0 0 22px;
                padding: 12px;
                border: 1px solid var(--border);
                border-radius: 8px;
                background: rgba(20, 26, 34, 0.78);
                position: sticky;
                top: 0;
                z-index: 10;
                backdrop-filter: blur(10px);
            }

            .report-nav a {
                display: inline-flex;
                align-items: center;
                min-height: 34px;
                padding: 6px 12px;
                border-radius: 6px;
                background: var(--bg-panel-soft);
                border: 1px solid var(--border);
                color: var(--text);
                text-decoration: none;
                font-weight: 600;
                font-size: 0.9em;
            }

            .report-nav a:hover {
                border-color: var(--accent);
                color: var(--accent-strong);
            }

            .report-nav a:focus-visible {
                outline: 2px solid var(--accent);
                outline-offset: 2px;
            }
            
            .executive-summary {
                background: linear-gradient(135deg, rgba(33, 199, 168, 0.10), rgba(245, 184, 75, 0.06)), var(--bg-panel);
                border-left: 5px solid var(--accent);
                padding: 25px;
                margin-bottom: 22px;
                border-radius: 8px;
                box-shadow: var(--shadow);
                border-top: 1px solid var(--border);
                border-right: 1px solid var(--border);
                border-bottom: 1px solid var(--border);
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

            .interactive-table-wrapper {
                display: flex;
                flex-direction: column;
                gap: 12px;
            }

            .table-controls {
                display: flex;
                justify-content: space-between;
                align-items: center;
                flex-wrap: wrap;
                gap: 10px;
            }

            .table-search {
                padding: 9px 14px;
                border: 1px solid var(--border);
                border-radius: 6px;
                font-size: 0.95em;
                min-width: 220px;
                background: var(--bg);
                color: var(--text);
                box-shadow: inset 0 1px 2px rgba(0,0,0,0.22);
            }

            .table-search:focus {
                outline: none;
                border-color: var(--accent);
                box-shadow: 0 0 0 0.2rem rgba(33,199,168,0.20);
            }

            .table-hint {
                font-size: 0.85em;
                color: var(--muted);
                display: flex;
                align-items: center;
                gap: 6px;
            }

            .table-hint::before {
                content: '\u2139';
                font-size: 1em;
                color: var(--info);
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

            th.sortable {
                cursor: pointer;
                position: relative;
                user-select: none;
            }

            th.sortable::after {
                content: '\u25B4\u25BE';
                position: absolute;
                right: 10px;
                font-size: 0.65em;
                opacity: 0.3;
            }

            th.sortable.sorted-asc::after {
                content: '\u25B2';
                opacity: 0.9;
            }

            th.sortable.sorted-desc::after {
                content: '\u25BC';
                opacity: 0.9;
            }

            /* The sort toggle is rendered as a real <button> inside the
               <th> so keyboard users can activate it with Enter/Space
               and screen readers announce it as a button.  Reset the
               native button chrome so it looks like the surrounding
               header cell. */
            th.sortable button.sort-toggle {
                background: transparent;
                border: 0;
                padding: 0;
                margin: 0;
                color: inherit;
                font: inherit;
                text-align: inherit;
                text-transform: inherit;
                letter-spacing: inherit;
                cursor: inherit;
                width: 100%;
                display: inline-flex;
                align-items: center;
                gap: 6px;
            }

            th.sortable button.sort-toggle:focus-visible {
                outline: 2px solid var(--accent);
                outline-offset: 2px;
                border-radius: 4px;
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
            
            code, pre {
                font-family: 'Cascadia Code', 'SFMono-Regular', Consolas, monospace;
            }

            code {
                color: #d6bcfa;
            }

            pre {
                background: #080b10;
                color: #d9e2ec;
                border: 1px solid var(--border);
                border-radius: 6px;
                padding: 12px;
                overflow-x: auto;
            }

            .risk-critical { background-color: rgba(255, 107, 122, 0.16); color: #ffb3bc; }
            .risk-high { background-color: rgba(245, 184, 75, 0.16); color: #ffd58a; }
            .risk-medium { background-color: rgba(99, 179, 237, 0.16); color: #a8d8ff; }
            .risk-low { background-color: rgba(56, 193, 114, 0.16); color: #9ae6b4; }
            .risk-unknown { background-color: rgba(166, 179, 194, 0.10); color: var(--muted); }
            
            .badge {
                display: inline-block;
                padding: 4px 12px;
                border-radius: 20px;
                font-size: 0.8em;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0;
            }
            
            .badge-success { background-color: var(--success); color: #06140c; }
            .badge-warning { background-color: var(--warning); color: #1f1600; }
            .badge-danger { background-color: var(--danger); color: #210308; }
            .badge-info { background-color: var(--info); color: #06111d; }
            .badge-secondary { background-color: #526070; color: white; }
            
            .recommendations {
                background: rgba(245, 184, 75, 0.10);
                border: 1px solid rgba(245, 184, 75, 0.38);
                border-radius: 8px;
                padding: 20px;
                margin: 20px 0;
            }
            
            .recommendations h4 {
                color: #ffd58a;
                margin-bottom: 15px;
            }
            
            .recommendation-item {
                background: var(--bg-panel);
                border-left: 4px solid var(--accent-warm);
                padding: 15px;
                margin: 10px 0;
                border-radius: 0 5px 5px 0;
            }
            
            .out-of-scope {
                background-color: rgba(255, 107, 122, 0.13);
                border-left: 4px solid var(--danger);
            }

            .host-execution-card {
                margin-bottom: 24px;
                border: 1px solid var(--border);
                border-radius: 8px;
                overflow: hidden;
                background: var(--bg-panel-soft);
            }

            .host-execution-header {
                padding: 14px 16px;
                background: linear-gradient(135deg, rgba(255,255,255,0.06), rgba(33,199,168,0.06));
                border-bottom: 1px solid var(--border);
            }

            .host-execution-title {
                display: flex;
                justify-content: space-between;
                align-items: center;
                flex-wrap: wrap;
                gap: 10px;
            }

            .host-execution-body {
                padding: 14px 16px;
            }

            .report-pill-row {
                display: flex;
                gap: 6px;
                flex-wrap: wrap;
            }

            .report-pill {
                background: #526070;
                color: white;
                padding: 3px 9px;
                border-radius: 999px;
                font-size: 0.8em;
                font-weight: 600;
            }

            .report-pill-info {
                background: var(--info);
                color: #06111d;
            }

            .report-pill-dark {
                background: #2f3b4a;
                color: var(--text);
            }

            .sanity-panel,
            .finding-panel {
                margin: 10px 0;
                padding: 12px;
                background: rgba(13, 17, 23, 0.55);
                border-left: 4px solid var(--accent);
                border-radius: 0 6px 6px 0;
            }

            .sanity-panel.failed {
                border-left-color: var(--danger);
            }

            .sanity-panel.missing {
                border-left-color: var(--warning);
            }

            .finding-panel {
                border-left-color: var(--accent-warm);
            }

            .muted-text {
                color: var(--muted);
            }
            
            .footer {
                text-align: center;
                padding: 30px;
                color: var(--muted);
                font-size: 0.9em;
                border-top: 1px solid var(--border);
                margin-top: 50px;
            }
            
            .logo {
                float: right;
                max-height: 60px;
                margin-left: 20px;
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
            
            .chart-placeholder {
                background: var(--bg-panel-soft);
                border: 2px dashed var(--border);
                border-radius: 8px;
                padding: 40px;
                text-align: center;
                color: var(--muted);
                margin: 20px 0;
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
                .section, .executive-summary { page-break-inside: avoid; box-shadow: none; }
                .report-header { background: #ffffff !important; color: var(--text); box-shadow: none; }
                .report-nav { display: none; }
                .stat-card:hover { transform: none; }
                tr:hover { background-color: transparent; }
                /* Override the dark <pre>/<code> theme so raw output prints
                   as dark text on light paper instead of soaking up toner. */
                pre, code {
                    background: #f4f6f8 !important;
                    color: #1f2933 !important;
                    border-color: #d9e2ec !important;
                }
            }
            
            @media (max-width: 768px) {
                .stats-grid { grid-template-columns: 1fr; }
                .metadata { flex-direction: column; align-items: flex-start; }
                .logo { float: none; margin: 10px 0; }
            }

            /* ---- Host dossiers (the host-centric report body) ------------- */
            .dossier-controls {
                position: sticky; top: 0; z-index: 5;
                display: flex; align-items: center; gap: 10px;
                padding: 10px 0; margin-bottom: 12px;
                background: var(--bg); border-bottom: 1px solid var(--border);
            }
            .dossier-search {
                flex: 1; min-width: 0; padding: 9px 12px;
                background: var(--bg-panel); color: var(--text);
                border: 1px solid var(--border); border-radius: 8px; font-size: 0.95em;
            }
            .dossier-search:focus { outline: none; border-color: var(--accent); }
            .dossier-nav-btn {
                flex: 0 0 auto; padding: 8px 12px; cursor: pointer;
                background: var(--bg-panel-soft); color: var(--text);
                border: 1px solid var(--border); border-radius: 8px;
            }
            .dossier-nav-btn:hover { border-color: var(--accent); }

            .host-dossiers { display: flex; flex-direction: column; gap: 16px; }
            .host-dossier {
                border: 1px solid var(--border); border-radius: 10px;
                background: var(--bg-panel); padding: 16px; scroll-margin-top: 70px;
            }
            .dossier-flash { box-shadow: 0 0 0 2px var(--accent); }
            .dossier-head { display: flex; flex-direction: column; gap: 8px; }
            .dossier-id { font-size: 1.25em; font-weight: 700; color: var(--text); word-break: break-all; }
            .dossier-id .anchor-self { color: var(--subtle); text-decoration: none; margin-right: 4px; }
            .dossier-host { font-size: 0.7em; font-weight: 500; color: var(--muted); }
            .dossier-meta { display: flex; flex-wrap: wrap; gap: 6px 20px; margin: 0; }
            .dossier-meta div { display: flex; gap: 6px; align-items: baseline; }
            .dossier-meta dt { color: var(--subtle); font-size: 0.78em; text-transform: uppercase; letter-spacing: 0.04em; margin: 0; }
            .dossier-meta dd { margin: 0; color: var(--text); font-size: 0.9em; }
            .dossier-meta dd.state-up { color: var(--success); font-weight: 600; }
            .dossier-meta dd.state-down { color: var(--danger); font-weight: 600; }
            .dossier-glance { display: flex; flex-direction: column; gap: 3px; font-size: 0.85em; color: var(--muted); }
            .dossier-glance strong { color: var(--text); }

            .dossier-block { margin-top: 12px; border-top: 1px solid var(--border); }
            .dossier-block > summary {
                cursor: pointer; padding: 8px 0; font-weight: 600; color: var(--text);
                list-style: none; display: flex; align-items: center; gap: 8px;
            }
            .dossier-block > summary::-webkit-details-marker { display: none; }
            .dossier-block > summary::before { content: '▸'; color: var(--subtle); }
            .dossier-block[open] > summary::before { content: '▾'; }
            .dossier-block-body { padding: 4px 0 10px; }
            .dcount {
                font-size: 0.78em; color: var(--muted); background: var(--bg-panel-soft);
                border: 1px solid var(--border); border-radius: 999px; padding: 1px 8px;
            }
            .dfinding, .dnote { padding: 8px 0; border-bottom: 1px dashed var(--border); }
            .dfinding:last-child, .dnote:last-child { border-bottom: none; }
            .dfinding-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
            .dfinding-title { color: var(--accent-strong); text-decoration: none; font-weight: 600; }
            .dfinding-title:hover { text-decoration: underline; }
            .dfinding-detail { margin-top: 4px; color: var(--muted); font-size: 0.88em; word-break: break-word; }
            .dfinding-detail code { background: var(--bg); padding: 1px 5px; border-radius: 4px; word-break: break-all; }
            .dcomment { margin-top: 6px; padding-left: 10px; border-left: 3px solid var(--border); font-size: 0.85em; color: var(--muted); }
            .dpill {
                font-size: 0.75em; color: var(--text); background: var(--bg-panel-soft);
                border: 1px solid var(--border); border-radius: 999px; padding: 1px 8px;
            }
            .dsev {
                font-size: 0.75em; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em;
                color: #0d1117; border-radius: 4px; padding: 1px 6px;
            }
            .dsev-critical { background: #ff6b7a; }
            .dsev-high { background: #f5894b; }
            .dsev-medium { background: #f7c948; }
            .dsev-low { background: #4fd1c5; }
            .dsev-info { background: #7d8a99; }
            .dsev-unknown, .dsev-none { background: #5a6776; color: #edf2f7; }

            @media print {
                .dossier-controls { display: none !important; }
                .host-dossier { break-inside: avoid; }
                /* WeasyPrint renders <details> bodies only when open — the
                   renderer also emits <details open>, this is belt-and-braces. */
                details.dossier-block > .dossier-block-body { display: block !important; }
            }
        </style>
        """

    @staticmethod
    def get_interactive_scripts() -> str:
        """Client-side helpers for table filtering and sorting"""
        return """
        <script>
            (function() {
                const initializeTables = () => {
                    document.querySelectorAll('.interactive-table-wrapper').forEach((wrapper) => {
                        const table = wrapper.querySelector('table.interactive-table');
                        if (!table) {
                            return;
                        }
                        const tbody = table.querySelector('tbody');
                        if (!tbody) {
                            return;
                        }

                        const headers = Array.from(table.querySelectorAll('thead th'));
                        const staticRows = Array.from(tbody.querySelectorAll('tr[data-static-row="true"]'));
                        const dynamicRows = () => Array.from(tbody.querySelectorAll('tr')).filter((row) => row.getAttribute('data-static-row') !== 'true');

                        const searchInput = wrapper.querySelector('.table-search');
                        if (searchInput) {
                            searchInput.addEventListener('input', (event) => {
                                applyFilter(dynamicRows(), staticRows, event.target.value || '');
                            });
                        }

                        headers.forEach((header, index) => {
                            header.classList.add('sortable');
                            // aria-sort communicates the current sort state to
                            // assistive tech.  Initial value is 'none' so screen
                            // readers know the column is sortable but not yet
                            // sorted.
                            header.setAttribute('aria-sort', 'none');

                            // Wrap the existing header content in a real
                            // <button> so keyboard users get Enter/Space
                            // activation for free and screen readers announce
                            // it as an interactive control.  Idempotent — if a
                            // sort-toggle button already wraps the content
                            // (re-init), skip the DOM rewrite.
                            let btn = header.querySelector('button.sort-toggle');
                            if (!btn) {
                                btn = document.createElement('button');
                                btn.type = 'button';
                                btn.className = 'sort-toggle';
                                while (header.firstChild) {
                                    btn.appendChild(header.firstChild);
                                }
                                header.appendChild(btn);
                            }

                            btn.addEventListener('click', () => {
                                const current = header.getAttribute('aria-sort') || 'none';
                                const next = current === 'ascending' ? 'descending' : 'ascending';

                                headers.forEach((h) => {
                                    h.setAttribute('aria-sort', 'none');
                                    h.classList.remove('sorted-asc', 'sorted-desc');
                                });

                                header.setAttribute('aria-sort', next);
                                header.classList.add(next === 'ascending' ? 'sorted-asc' : 'sorted-desc');

                                const rows = dynamicRows();
                                rows.sort((a, b) => {
                                    const aValue = toComparable(a.children[index] ? a.children[index].innerText : '');
                                    const bValue = toComparable(b.children[index] ? b.children[index].innerText : '');

                                    let comparison = 0;
                                    if (aValue.type === 'number' && bValue.type === 'number') {
                                        comparison = aValue.value - bValue.value;
                                    } else {
                                        comparison = aValue.value.localeCompare(bValue.value, undefined, { numeric: true, sensitivity: 'base' });
                                    }

                                    return next === 'ascending' ? comparison : -comparison;
                                });

                                rows.forEach((row) => tbody.appendChild(row));
                                staticRows.forEach((row) => tbody.appendChild(row));
                            });
                        });

                        applyFilter(dynamicRows(), staticRows, searchInput ? searchInput.value || '' : '');
                    });
                };

                const toComparable = (raw) => {
                    const value = (raw || '').trim();
                    const numeric = parseFloat(value.replace(/[^0-9.-]/g, ''));
                    if (!Number.isNaN(numeric) && /^-?\\d/.test(value)) {
                        return { type: 'number', value: numeric };
                    }
                    return { type: 'string', value: value.toLowerCase() };
                };

                const applyFilter = (rows, staticRows, term) => {
                    const query = term.trim().toLowerCase();
                    rows.forEach((row) => {
                        const text = row.innerText.toLowerCase();
                        row.style.display = text.includes(query) ? '' : 'none';
                    });

                    if (staticRows.length) {
                        const display = query ? 'none' : '';
                        staticRows.forEach((row) => {
                            row.style.display = display;
                        });
                    }
                };

                // Report-wide host search over the dossiers: matches each host's
                // pre-built data-search blob (IP/hostname/site/subnet/CVE/finding
                // title/note text/service), auto-expands the sub-blocks of a
                // match, counts results, and Enter / the arrow buttons step
                // through matches.
                const initializeDossierSearch = () => {
                    const input = document.getElementById('host-search');
                    const container = document.querySelector('.host-dossiers');
                    if (!input || !container) {
                        return;
                    }
                    const sections = Array.from(container.querySelectorAll('.host-dossier'));
                    const countEl = document.getElementById('host-search-count');
                    let matches = sections.slice();
                    let cursor = -1;

                    const apply = (raw) => {
                        const query = (raw || '').trim().toLowerCase();
                        matches = [];
                        sections.forEach((sec) => {
                            const blob = sec.getAttribute('data-search') || '';
                            const hit = !query || blob.indexOf(query) !== -1;
                            sec.style.display = hit ? '' : 'none';
                            if (hit) {
                                matches.push(sec);
                                if (query) {
                                    sec.querySelectorAll('details.dossier-block').forEach((d) => { d.open = true; });
                                }
                            }
                        });
                        cursor = -1;
                        if (countEl) {
                            countEl.textContent = query
                                ? (matches.length + ' / ' + sections.length + ' hosts')
                                : (sections.length + ' hosts');
                        }
                    };

                    const jump = (delta) => {
                        if (!matches.length) {
                            return;
                        }
                        cursor = (cursor + delta + matches.length) % matches.length;
                        const target = matches[cursor];
                        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                        target.classList.add('dossier-flash');
                        setTimeout(() => target.classList.remove('dossier-flash'), 1200);
                    };

                    input.addEventListener('input', (e) => apply(e.target.value));
                    input.addEventListener('keydown', (e) => {
                        if (e.key === 'Enter') {
                            e.preventDefault();
                            jump(e.shiftKey ? -1 : 1);
                        }
                    });
                    const next = document.getElementById('host-search-next');
                    const prev = document.getElementById('host-search-prev');
                    if (next) next.addEventListener('click', () => jump(1));
                    if (prev) prev.addEventListener('click', () => jump(-1));
                    apply('');
                };

                const initializeAll = () => { initializeTables(); initializeDossierSearch(); };

                if (document.readyState === 'loading') {
                    document.addEventListener('DOMContentLoaded', initializeAll);
                } else {
                    initializeAll();
                }
            })();
        </script>
        """
