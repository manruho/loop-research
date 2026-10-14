"""Escaped LaTeX export and optional isolated LuaLaTeX/BibTeX compilation."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from .common import ResearchError


def escape(text):
    substitutions = {'\\': r'\textbackslash{}', '{': r'\{', '}': r'\}', '$': r'\$', '&': r'\&',
                     '%': r'\%', '#': r'\#', '_': r'\_', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    normalized = str(text).replace('\r\n', '\n').replace('\r', '\n')
    return ''.join(substitutions.get(c, c) for c in normalized)


def render(store, artifact, status, current):
    content = artifact['content'] if artifact else None
    title = content['title'] if content else 'Research outcome'
    lines = [r'\documentclass[11pt,a4paper]{article}', r'\usepackage{luatexja}',
             r'\usepackage[margin=25mm]{geometry}', r'\usepackage[numbers,sort&compress]{natbib}',
             r'\usepackage[hidelinks]{hyperref}', r'\setlength{\emergencystretch}{3em}',
             r'\title{' + escape(title) + '}', r'\author{}', r'\date{}', r'\begin{document}', r'\maketitle',
             r'\begin{quote}\small Research status: ' + escape(status) + '. '
             + ('Current research draft. Internal AI review is not academic approval.' if current else 'Historical or incomplete output; do not treat as the current research conclusion.') + r'\end{quote}']
    trace, manual = [], {}
    metadata_trace = [{'block_id': 'MT001', 'text': title, 'tex_first_line': 7, 'tex_last_line': 7}] if content else []
    def block_text(block):
        keys = []
        for eid in block['evidence_ids']:
            evidence = store.show_evidence(eid)
            source = evidence['document_source']
            key = source['paper_id'] if source else eid
            if not source:
                manual[key] = evidence
            if key not in keys:
                keys.append(key)
        first = len(lines) + 1
        lines.append('% block: ' + block['id'])
        lines.append(escape(block['text']) + (r' \citep{' + ','.join(keys) + '}' if keys else ''))
        lines.append('')
        if block['qualification'].strip():
            lines.extend([r'\begin{quote}\small ' + escape(block['qualification']) + r'\end{quote}', ''])
        trace.append({'block_id': block['id'], 'tex_first_line': first, 'tex_last_line': len(lines),
                      'claim_ids': block['claim_ids'], 'evidence_ids': block['evidence_ids'], 'bib_keys': keys,
                      'text': block['text'], 'qualification': block['qualification']})
    if content:
        lines.append(r'\begin{abstract}')
        for block in content['abstract_blocks']:
            block_text(block)
        lines.append(r'\end{abstract}')
        for i, section in enumerate(content['sections'], 1):
            lines.append(r'\section{' + escape(section['heading']) + '}')
            metadata_trace.append({'block_id': f'MH{i:03}', 'text': section['heading'],
                                   'tex_first_line': len(lines), 'tex_last_line': len(lines)})
            for block in section['blocks']:
                block_text(block)
        lines.extend([r'\section*{Outstanding uncertainties}', r'\begin{itemize}'])
        for i, value in enumerate(content['uncertainties'], 1):
            lines.append(r'\item ' + escape(value))
            metadata_trace.append({'block_id': f'MU{i:03}', 'text': value,
                                   'tex_first_line': len(lines), 'tex_last_line': len(lines)})
        lines.append(r'\end{itemize}')
    else:
        lines.append('No manuscript has been produced.')
    if any(t['bib_keys'] for t in trace):
        lines.extend([r'\bibliographystyle{plainnat}', r'\bibliography{references}'])
    lines.append(r'\end{document}')
    physical_lines, offset = [], 1
    for entry in lines:
        physical_lines.append(offset)
        offset += entry.count('\n') + 1
    for item in trace + metadata_trace:
        first, last = item['tex_first_line'] - 1, item['tex_last_line'] - 1
        item['tex_first_line'] = physical_lines[first]
        item['tex_last_line'] = physical_lines[last] + lines[last].count('\n')
    return '\n'.join(lines) + '\n', {'draft_id': artifact['id'] if artifact else None, 'current': current,
                                    'status': status, 'blocks': trace, 'metadata_targets': metadata_trace,
                                    'notice': 'TeX line locations refer to this export only. Edit the structured manuscript and reverify changes; editing TeX does not update the research ledger.'}, manual


def manual_bibliography(entries):
    return ''.join('@misc{' + key + ',\n  title = {' + escape(e['source']) + '},\n  note = {'
                   + escape('Manually supplied attribution; not independently verified. Locator: ' + e['locator']) + '}\n}\n'
                   for key, e in entries.items())


def compile_pdf(stage, timeout=120):
    latex, bibtex = shutil.which('lualatex'), shutil.which('bibtex')
    if not latex or not bibtex:
        raise ResearchError('PDF生成にはLuaLaTeXとBibTeXが必要です。--pdfを外すとLaTeXソースを出力できます。')
    with tempfile.TemporaryDirectory(prefix='.latex-build-', dir=stage) as temporary:
        work = Path(temporary)
        for name in ('paper.tex', 'references.bib'):
            shutil.copyfile(stage / name, work / name)
        env = dict(os.environ, openin_any='p', openout_any='p')
        commands = [[latex, '-no-shell-escape', '-interaction=nonstopmode', '-halt-on-error', 'paper.tex']]
        if r'\citep{' in (work / 'paper.tex').read_text():
            commands.append([bibtex, 'paper'])
        commands.extend([[latex, '-no-shell-escape', '-interaction=nonstopmode', '-halt-on-error', 'paper.tex']] * 2)
        logs = []
        for command in commands:
            try:
                result = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                raise ResearchError('LaTeXのPDF生成が時間上限を超えました。') from exc
            logs.append(result.stdout + result.stderr)
            if result.returncode:
                raise ResearchError('LaTeXのPDF生成に失敗しました:\n' + logs[-1][-2000:])
        log = (work / 'paper.log').read_text(errors='replace')
        if 'Missing character:' in log or 'There were undefined references' in log or 'Citation' in log and 'undefined' in log:
            raise ResearchError('PDFの文字・引用に未解決の問題があります:\n' + '\n'.join(line for line in log.splitlines() if 'Missing character:' in line or 'undefined' in line)[-1500:])
        shutil.copyfile(work / 'paper.pdf', stage / 'paper.pdf')
        (stage / 'latex-build.log').write_text('\n'.join(logs))
        return {'engine': 'lualatex', 'bibliography': 'bibtex', 'shell_escape': False}
