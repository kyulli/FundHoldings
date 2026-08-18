"""HTML rendering for the mapping review UI (EN default, bilingual)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def esc(value: Any) -> str:
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def bi(en: str, zh: str) -> str:
    """Inline bilingual span; JS toggles .lang-en / .lang-zh visibility."""
    return f'<span class="lang-en">{esc(en)}</span><span class="lang-zh">{esc(zh)}</span>'


def amount_table_html(rows: list[dict[str, Any]], *, soft_missing: bool = False) -> str:
    if not rows:
        return ""
    row_parts = []
    for a in rows:
        status = a.get("status")
        if soft_missing and status == "csv_missing":
            status_cls = "warn"
        elif status == "match":
            status_cls = "ok"
        else:
            status_cls = "bad"
        excel_val = a.get("excel_value")
        excel_disp = bi("(blank)", "（空）") if excel_val is None else esc(excel_val)
        row_parts.append(
            "<tr>"
            f"<td>{esc(a.get('pdf_column'))}<div class='meta'>PDF</div></td>"
            f"<td>{esc(a.get('pdf_value'))}</td>"
            f"<td>{esc(a.get('excel_column'))}<div class='meta'>Excel</div></td>"
            f"<td>{excel_disp}</td>"
            f"<td class='{status_cls}'>{esc(a.get('status_label'))}</td>"
            f"<td><details class='help'><summary>{bi('How is this calculated?', '怎么算的？')}</summary>"
            f"<div class='body'>{esc(a.get('calculation') or a.get('notes') or '—')}</div></details></td>"
            "</tr>"
        )
    return f"""
    <table class="amt">
      <thead><tr>
        <th>{bi('PDF field', 'PDF 字段')}</th><th>{bi('PDF value', 'PDF 值')}</th>
        <th>{bi('Excel column', 'Excel 列')}</th><th>{bi('Excel value', 'Excel 值')}</th>
        <th>{bi('Result', '结果')}</th><th>{bi('Notes', '说明')}</th>
      </tr></thead>
      <tbody>{''.join(row_parts)}</tbody>
    </table>
    """


def entity_card_html(e: dict[str, Any], *, show_actions: bool) -> str:
    ui = e.get("ui") or {}
    status_key = e.get("user_decision") or e.get("status")
    pill_cls = (
        "auto"
        if status_key in {"auto_accept", "accept"}
        else ("review" if status_key in {"needs_review"} else "unmapped")
    )
    status_disp = bi(ui.get("status_label_en") or status_key or "", ui.get("status_label_zh") or ui.get("status_label") or "")
    match_disp = bi(ui.get("match_label_en") or "", ui.get("match_label_zh") or ui.get("match_label") or "")
    cands = e.get("candidates") or []
    rec = e.get("user_vendor_source_asset") or e.get("recommended_vendor_source_asset") or ""
    sel_id = f"sel-{abs(hash(e.get('pdf_company_name')))}"
    risk_bits = "".join(
        f"<li><b>{esc(r.get('label'))}</b> — {esc(r.get('explanation'))}</li>" for r in (ui.get("risks") or [])
    ) or f"<li>{bi('None', '无')}</li>"

    primary = ui.get("primary_amount_rows") or ui.get("amount_rows") or []
    secondary = ui.get("secondary_amount_rows") or []
    amount_table = amount_table_html(primary, soft_missing=False)
    if secondary:
        amount_table += f"""
        <details class="help muted-details">
          <summary>{bi('Optional derived fields (Realized / Capital Invested / Total Value) — usually ignore when Excel is blank', '其他派生字段（Realized / Capital Invested / Total Value）— Excel 常为空，一般可忽略')}</summary>
          <div class="body">
            <p>{bi('For still-held positions, primary reconcile is Current Cost & Unrealized Value. PDF 0 on realized fields means “not on realized schedule”.', '仍持有仓位时，主对账看 Current Cost / Unrealized Value。PDF 已实现字段为 0 表示不在已实现表。')}</p>
            {amount_table_html(secondary, soft_missing=True)}
          </div>
        </details>
        """

    fund_banner = ""
    if ui.get("is_fund_self"):
        fund_banner = f"""
        <div class="callout">
          <b>{bi('Likely the FUND itself, not a portfolio company', '更像是本基金本身，不是被投公司')}</b>
          <div class="meta">{bi(ui.get('identity_en') or '', ui.get('identity_zh') or '')}</div>
          <div class="meta">{bi('There is nothing to map in Excel. Confirm below that this row is not a holding.', 'Excel 里没有可对的持仓。请在下方确认：这一行不是持仓。')}</div>
        </div>
        """

    actions = ""
    if show_actions:
        no_cands = not cands
        is_fund = bool(ui.get("is_fund_self") or ui.get("suggested_action") == "not_vendor_holding")

        if is_fund or no_cands:
            # Single clear path: nothing to pick in Excel.
            actions = f"""
            <div class="grow action-panel">
              <p class="action-hint">
                {bi(
                  'No Excel Source Asset to pick. Click the button to confirm this PDF name is not a holdings row.',
                  '没有可选择的 Excel Source Asset。点下面按钮，确认这个 PDF 名称不是持仓行。'
                )}
              </p>
              <div class="actions">
                <button type="button" class="primary" data-action="decide" data-pdf="{esc(e.get('pdf_company_name'))}" data-decide="not_vendor_holding">
                  {bi('Confirm: not an Excel holding', '确认：不是 Excel 持仓')}
                </button>
              </div>
              <details class="help muted-details">
                <summary>{bi('Other options', '其他选项')}</summary>
                <div class="body actions">
                  <button type="button" data-action="decide" data-pdf="{esc(e.get('pdf_company_name'))}" data-decide="leave_unmapped">{bi('Skip this time', '本次先跳过')}</button>
                </div>
              </details>
            </div>
            """
        else:
            options = "".join(
                f'<option value="{esc(c.get("vendor_source_asset"))}">{esc(c.get("vendor_source_asset"))}'
                + (f' · {round(float(c.get("score"))*100):.0f}%' if c.get("score") is not None else "")
                + "</option>"
                for c in cands
            )
            actions = f"""
            <div class="grow action-panel">
              <label>{bi('Pick Excel “Source Asset”', '选择 Excel「Source Asset」')}</label>
              <select id="{sel_id}">{options}</select>
              <div class="actions">
                <button type="button" class="primary" data-action="accept" data-pdf="{esc(e.get('pdf_company_name'))}" data-sel="{sel_id}" data-fallback="{esc(rec)}">{bi('Accept this Excel name', '确认对应到这个 Excel 名称')}</button>
                <button type="button" data-action="decide" data-pdf="{esc(e.get('pdf_company_name'))}" data-decide="not_vendor_holding">{bi('Not an Excel holding', '不是 Excel 持仓')}</button>
                <button type="button" data-action="decide" data-pdf="{esc(e.get('pdf_company_name'))}" data-decide="leave_unmapped">{bi('Skip this time', '本次不映射')}</button>
              </div>
            </div>
            """

    return f"""
    <div class="card {'fund-self' if ui.get('is_fund_self') else ''}">
      <div class="row">
        <div class="grow">
          <div>
            <strong>{esc(e.get('pdf_company_name'))}</strong>
            <span class="pill {pill_cls}">{status_disp}</span>
            <span class="pill">{match_disp}</span>
          </div>
          {fund_banner}
          <div class="pair">
            <div class="side">
              <label>{bi('PDF schedule name', 'PDF 持仓表名称')}</label>
              <strong>{esc(e.get('pdf_company_name'))}</strong>
            </div>
            <div class="arrow">→</div>
            <div class="side">
              <label>{bi('Excel Source Asset', 'Excel Source Asset')}</label>
              <strong>{esc(rec) if rec else bi('(none yet)', '（尚未选择）')}</strong>
            </div>
          </div>
          <div class="kv">
            <span>PDF Cost: <b>{esc(ui.get('pdf_cost') if ui.get('pdf_cost') is not None else '—')}</b></span>
            <span>PDF Fair Value: <b>{esc(ui.get('pdf_fair_value') if ui.get('pdf_fair_value') is not None else '—')}</b></span>
            <span>{bi('Page', '页码')}: <b>{esc(ui.get('pages') or '—')}</b></span>
          </div>
          <details class="help muted-details">
            <summary>{bi('Why this status?', '为什么是这个状态？')}</summary>
            <div class="body">
              <p>{esc(ui.get('why') or '')}</p>
              <p><b>{bi('Risks', '风险提示')}</b></p>
              <ul>{risk_bits}</ul>
            </div>
          </details>
          {amount_table}
        </div>
        {actions}
      </div>
    </div>
    """


def _human_promote(preview: dict[str, Any] | None) -> str:
    if not preview or preview.get("error"):
        return bi(
            "Available after you finish name review and a successful draft reconcile.",
            "先完成名称审批，并成功试跑对账后，这里才会显示详情。",
        )
    mapping_path = preview.get("target_mapping_path") or ""
    n = preview.get("entity_mapping_count")
    aliases = preview.get("fund_scoped_alias_updates") or {}
    alias_lines = "".join(f"<li><code>{esc(k)}</code> → <code>{esc(v)}</code></li>" for k, v in list(aliases.items())[:12])
    if not alias_lines:
        alias_lines = f"<li>{bi('(no new nicknames to save)', '（没有新的名称对应要额外保存）')}</li>"
    return f"""
    <ul class="plain">
      <li>{bi(f'Save {n} confirmed PDF ↔ Excel name matches for this fund.', f'为该基金保存 {n} 条已确认的 PDF ↔ Excel 名称对应。')}</li>
      <li>{bi('Remember nicknames only for this fund (not for every fund):', '只记住本基金的名称对应（不会影响其他基金）：')}
        <ul>{alias_lines}</ul></li>
      <li class="meta">{bi('Saved file (for engineers):', '保存位置（给工程师看）：')} <code>{esc(mapping_path)}</code></li>
    </ul>
    <p class="meta">{bi('This does not change any PDF or Excel amounts. It only remembers which names belong together, so the next statement for this fund usually skips this review.', '不会改 PDF / Excel 里的任何金额。只记住名字怎么对应，下次同基金报表通常就不用再审一遍。')}</p>
    """


def extraction_gate_html(gate: dict[str, Any] | None) -> str:
    """Prominent panel when extract needs a human decision (not name mapping)."""
    if not gate:
        return ""
    pages = list(gate.get("schedule_pages") or [])
    reasons = list(gate.get("reasons") or [])
    page_disp = (
        esc(", ".join(str(p) for p in pages))
        if pages
        else bi("(none found)", "（没找到）")
    )
    reason_lis = "".join(f"<li><code>{esc(r)}</code></li>" for r in reasons[:8]) or f"<li>{bi('(none)', '（无）')}</li>"
    pdf_name = Path(str(gate.get("pdf_path") or "")).name if gate.get("pdf_path") else "—"

    should_lis = "".join(
        f"<li>{bi(en, zh)}</li>"
        for en, zh in zip(gate.get("you_should_en") or [], gate.get("you_should_zh") or gate.get("you_should_en") or [])
    )
    should_not_lis = "".join(
        f"<li>{bi(en, zh)}</li>"
        for en, zh in zip(
            gate.get("you_should_not_en") or [],
            gate.get("you_should_not_zh") or gate.get("you_should_not_en") or [],
        )
    )
    primary = gate.get("primary_action") or "needs_engineer"
    # Auto-skippable cases should not ask for a confirm click.
    if primary == "auto_skipped_not_holdings":
        primary_btn = f"""
          <p class="ok">{bi('Already auto-skipped — nothing to click.', '已自动跳过 — 无需点击。')}</p>
        """
    else:
        primary_btn = f"""
          <button type="button" class="primary" data-action="ack-extract" data-ack="needs_engineer">
            {bi('Needs engineer help', '需要工程师帮忙')}
          </button>
          <button type="button" data-action="ack-extract" data-ack="skip_no_holdings_schedule">
            {bi('Actually: no holdings table — skip', '其实没有持仓表 — 跳过')}
          </button>
        """

    return f"""
    <div class="card extraction-gate">
      <h2 class="warn">{bi(gate.get('verdict_en') or 'Human review required', gate.get('verdict_zh') or '需要人工判断')}</h2>
      <p>{bi(gate.get('why_en') or '', gate.get('why_zh') or '')}</p>
      <div class="kv">
        <span>{bi('PDF', 'PDF')}: <b>{esc(pdf_name)}</b></span>
        <span>{bi('Candidate holdings pages', '疑似持仓页')}: <b>{page_disp}</b></span>
      </div>
      <div class="row" style="margin-top:12px">
        <div class="grow">
          <h3 style="margin:0 0 6px;font-size:14px">{bi('You should', '你该做')}</h3>
          <ol class="plain">{should_lis}</ol>
        </div>
        <div class="grow">
          <h3 style="margin:0 0 6px;font-size:14px">{bi('You should NOT', '你不用做')}</h3>
          <ul class="plain">{should_not_lis}</ul>
        </div>
      </div>
      <div class="actions" style="margin-top:14px">{primary_btn}</div>
      <details class="help muted-details">
        <summary>{bi('Technical detail (optional)', '技术细节（可选）')}</summary>
        <div class="body">
          <p><b>{bi('Router reasons', '路由原因')}</b></p>
          <ul class="plain compact">{reason_lis}</ul>
        </div>
      </details>
    </div>
    """


def discrepancy_card_html(item: dict[str, Any]) -> str:
    """Side-by-side PDF vs Excel amount proposal card."""
    ev = item.get("evidence") or {}
    ev_status = ev.get("status") or "unlocated"
    page = (item.get("pages") or [None])[0] or ev.get("page")
    can_adopt_pdf = ev_status == "located"
    evidence_note = {
        "located": bi("PDF amount uniquely located on the page.", "PDF 金额已在页面上唯一定位。"),
        "ambiguous": bi("Multiple PDF matches — cannot auto-recommend PDF.", "PDF 有多处匹配 — 不能自动建议采用 PDF。"),
        "unlocated": bi("Could not locate this PDF amount — cannot recommend adopting PDF.", "未能定位此 PDF 金额 — 不能建议采用 PDF。"),
        "not_searched": bi("Evidence not searched yet.", "尚未搜索证据。"),
    }.get(ev_status, bi("Evidence unknown.", "证据状态未知。"))

    user = item.get("user_decision") or {}
    decided = bool(user.get("action"))
    actions = ""
    if item.get("needs_action") and not decided:
        adopt_pdf_btn = (
            f'<button type="button" class="primary" data-action="decide-disc" data-item="{esc(item.get("item_id"))}" data-decide="adopt_pdf">{bi("Adopt PDF value", "采用 PDF 值")}</button>'
            if can_adopt_pdf
            else f'<button type="button" disabled title="PDF evidence not uniquely located">{bi("Adopt PDF (blocked)", "采用 PDF（不可用）")}</button>'
        )
        actions = f"""
        <div class="actions">
          {adopt_pdf_btn}
          <button type="button" data-action="decide-disc" data-item="{esc(item.get('item_id'))}" data-decide="adopt_excel">{bi('Adopt Excel value', '采用 Excel 值')}</button>
          <button type="button" data-action="decide-disc" data-item="{esc(item.get('item_id'))}" data-decide="keep_real_discrepancy">{bi('Keep real discrepancy', '保留真实差异')}</button>
          <button type="button" data-action="decide-disc" data-item="{esc(item.get('item_id'))}" data-decide="pdf_evidence_unreliable">{bi('PDF evidence unreliable', 'PDF 证据不可靠')}</button>
        </div>
        """
    elif decided:
        actions = f'<p class="ok">{bi("Decision saved", "决定已保存")}: <code>{esc(user.get("action"))}</code></p>'

    return f"""
    <div class="card disc-card">
      <div class="row">
        <div class="grow">
          <strong>{esc(item.get('pdf_company_name'))}</strong>
          <span class="pill review">{esc(item.get('logical_field'))}</span>
          <span class="pill">{esc(item.get('status'))}</span>
          <div class="meta">{bi('You are deciding which value to record in the reviewed extraction Excel (vendor Excel is never modified).', '你在决定写入「已审核提取 Excel」的最终值（绝不改第三方 Excel）。')}</div>
          <div class="disc-grid">
            <div class="disc-side">
              <label>{bi('PDF extracted', 'PDF 提取')}</label>
              <div><b>{esc(item.get('pdf_value'))}</b></div>
              <div class="meta">{esc(item.get('pdf_field'))} · page {esc(page)}</div>
              <button type="button" data-action="preview" data-page="{esc(page)}" data-item="{esc(item.get('item_id'))}">{bi('Show in PDF', '在 PDF 中查看')}</button>
            </div>
            <div class="disc-side">
              <label>{bi('Excel (vendor)', 'Excel（第三方）')}</label>
              <div><b>{esc(item.get('excel_value'))}</b></div>
              <div class="meta">{esc(item.get('excel_field'))} · {esc(item.get('vendor_source_asset'))}</div>
              <div class="meta">{bi('As of', '报告日')}: {esc(item.get('as_of_date'))}</div>
            </div>
          </div>
          <div class="kv">
            <span>{bi('Difference', '差额')}: <b>{esc(item.get('difference'))}</b></span>
            <span>{bi('Tolerance', '容差')}: <b>{esc(item.get('tolerance'))}</b></span>
            <span>{bi('Suggestion', '系统建议')}: <b>{bi(item.get('suggestion_en') or '', item.get('suggestion_zh') or '')}</b></span>
          </div>
          <p class="meta">{evidence_note}</p>
          {actions}
        </div>
      </div>
    </div>
    """


def preview_pane_html(payload: dict[str, Any]) -> str:
    preview = payload.get("preview") or {}
    pending = list(payload.get("discrepancy_pending") or [])
    focus = pending[0] if pending else None
    focus_page = None
    focus_item = None
    if focus:
        focus_item = focus.get("item_id")
        pages = focus.get("pages") or []
        focus_page = pages[0] if pages else (focus.get("evidence") or {}).get("page")
    if not preview.get("available"):
        return f"""
        <aside class="preview-pane card">
          <h2>{bi('PDF preview', 'PDF 预览')}</h2>
          <p class="meta">{bi('Source PDF not available for preview.', '无法预览源 PDF。')}</p>
        </aside>
        """
    page = focus_page or preview.get("default_page") or 1
    return f"""
    <aside class="preview-pane">
      <div class="card">
        <h2>{bi('PDF preview', 'PDF 预览')}</h2>
        <div class="meta">{esc(preview.get('pdf_name'))} · {bi('page', '页')} <b id="previewPageLabel">{esc(page)}</b> / {esc(preview.get('page_count'))}</div>
        <div class="meta">{bi('Only the current discrepancy amount is highlighted.', '只高亮当前这一处待确认金额。')}</div>
        <div id="previewBoot" data-page="{esc(page)}" data-item="{esc(focus_item or '')}"></div>
        <div class="preview-frame">
          <img id="previewImg" alt="pdf page" src="/api/preview?page={esc(page)}"/>
          <div id="hlLayer"></div>
        </div>
        <p class="meta">{bi('Click “Show in PDF” on a card to jump and highlight that one value.', '点卡片上的「在 PDF 中查看」会跳转并只高亮那一个数字。')}</p>
      </div>
    </aside>
    """


def render_index_body(payload: dict[str, Any], promote_preview: dict[str, Any] | None) -> str:
    summary_ui = payload.get("summary_ui") or {}
    compare = payload.get("compare_summary") or {}
    route_ui = payload.get("route_ui") or {}
    entities = payload.get("entities") or []
    glossary = payload.get("glossary") or {}
    fund_name = payload.get("fund_name") or ""
    extraction_gate = payload.get("extraction_gate")
    extraction_ack = payload.get("extraction_ack") or {}

    needs = [e for e in entities if (e.get("ui") or {}).get("needs_action")]
    auto_done = [e for e in entities if not (e.get("ui") or {}).get("needs_action")]
    needs_cards = "".join(entity_card_html(e, show_actions=True) for e in needs)
    auto_list = "".join(
        f"<li><code>{esc(e.get('pdf_company_name'))}</code> → <code>{esc(e.get('recommended_vendor_source_asset') or e.get('user_vendor_source_asset') or '—')}</code></li>"
        for e in auto_done
    )
    gate_block = extraction_gate_html(extraction_gate)

    compare_block = ""
    if compare and not extraction_gate:
        fails = [g for g in (compare.get("gates") or []) if g.get("status") == "FAIL"]
        fail_human = [{"check": g.get("gate"), "reason": g.get("reason")} for g in fails]
        compare_block = f"""
        <div class="card">
          <h2>{bi('Step 2 — amount check (Current Cost / Unrealized Value)', '第 2 步 — 金额核对（Current Cost / Unrealized Value）')}</h2>
          <div class="meta">
            <span>{bi('Comparability', '可比性')}: {esc(compare.get('comparability_status'))}</span>
            <span>{bi('Mapped cos', '已映射公司')}: {esc(compare.get('accepted_mappings'))}</span>
            <span>{bi('Amount mismatches', '金额不一致')}: {esc(compare.get('amount_mismatch_count'))}</span>
          </div>
          <pre>{esc(json.dumps(fail_human, indent=2, ensure_ascii=False)) if fail_human else bi('No failed gates.', '门禁全部通过。')}</pre>
        </div>
        """

    glossary_rows = "".join(
        f"<tr><td><b>{esc(f.get('excel'))}</b></td><td>{esc(f.get('pdf'))}</td>"
        f"<td>{esc(f.get('explanation'))}"
        f"<details class='help'><summary>{bi('Formula / rule', '计算公式 / 规则')}</summary>"
        f"<div class='body'>{esc(f.get('calculation'))}</div></details></td></tr>"
        for f in (glossary.get("fields") or [])
    )

    promote_block = ""
    promotion = payload.get("promotion") or {}
    pending_disc = list(payload.get("discrepancy_pending") or [])
    has_pending_disc = bool(pending_disc)
    if extraction_gate:
        promote_block = ""
    elif has_pending_disc:
        # Name mapping may already be saved — amount review is NOT finished.
        if payload.get("promoted"):
            n = promotion.get("entity_mapping_count") or "—"
            promote_block = f"""
        <section id="step-save" class="card">
          <h2>{bi('Name matches saved — amount review still open', '名称已保存 — 金额审核未完成')}</h2>
          <p>{bi(
            f'Name matches ({n}) are remembered, but there is still at least one PDF ↔ Excel amount discrepancy to decide. Do not close this tab yet.',
            f'名称对应（{n}）已记住，但仍有至少一处 PDF ↔ Excel 金额差异待决定。先别关这个网页。'
          )}</p>
        </section>
        """
        else:
            promote_block = f"""
        <p id="step-save" class="meta footer-note">{bi(
          'Finish amount discrepancy decisions first. Name-match save unlocks after that.',
          '先完成金额差异决定。名称保存要等金额审完。'
        )}</p>
        """
    elif (payload.get("extraction_ack") or {}).get("action"):
        ack = payload.get("extraction_ack") or {}
        promote_block = f"""
        <section id="step-save" class="success-zone card">
          <h2 class="ok">{bi('✓ Recorded — you can close this tab', '✓ 已记录 — 可以关掉这个网页')}</h2>
          <p>{bi(
            f'Decision: {ack.get("action")}. This PDF will not ask for holdings name review again.',
            f'判断：{ack.get("action")}。这份 PDF 不会再来烦你做持仓名称审批。'
          )}</p>
        </section>
        """
    elif payload.get("promoted"):
        n = promotion.get("entity_mapping_count") or (promotion.get("preview") or {}).get("entity_mapping_count") or "—"
        reviewer = promotion.get("reviewer") or "—"
        promote_block = f"""
        <section id="step-save" class="success-zone card">
          <h2 class="ok">{bi('✓ Saved — this fund is finished', '✓ 已保存 — 这个基金的流程结束了')}</h2>
          <p>{bi('Name matches are remembered for this fund. You usually will not need to re-review them next time.', '本基金的名称对应已记住。下次通常不用再审一遍。')}</p>
          <div class="meta">
            <span>{bi('Saved by', '保存人')}: <b>{esc(reviewer)}</b></span>
            <span>{bi('Name matches kept', '已保存名称数')}: <b>{esc(n)}</b></span>
          </div>
          <p class="meta">{bi('You can close this tab.', '可以关掉这个网页了。')}</p>
        </section>
        """
    elif payload.get("promote_ready"):
        promote_block = f"""
        <section id="step-save" class="promote-zone card">
          <h2>{bi('Step 3 — save these name matches', '第 3 步 — 保存这些名称对应')}</h2>
          <p class="meta">{bi('Enter your name, then click Save. After saving, this page will show a green “finished” banner.', '先填名字，再点保存。成功后页面会变成绿色「已结束」横幅。')}</p>
          <label class="meta" for="reviewerName">{bi('Your name', '你的名字')}</label>
          <input id="reviewerName" type="text" placeholder="Your name / 你的名字" autocomplete="name"/>
          <div class="actions">
            <button type="button" class="primary" data-action="promote">{bi('Save name matches for this fund', '保存本基金的名称对应')}</button>
          </div>
          <details class="help muted-details">
            <summary>{bi('What does this button do?', '这个按钮会做什么？')}</summary>
            <div class="body">{_human_promote(promote_preview)}</div>
          </details>
        </section>
        """
    else:
        promote_block = f"""
        <p id="step-save" class="meta footer-note">{bi('Step 3 unlocks after Step 1 is done and Step 2 amount check succeeds.', '完成第 1 步，且第 2 步金额核对成功后，才会出现第 3 步保存。')}</p>
        """

    short_fund = fund_name
    if short_fund and len(short_fund) > 80:
        short_fund = short_fund[:80] + "…"

    todo_n = len(needs)
    comparable = (compare or {}).get("comparability_status") == "comparable"
    promote_ready = bool(payload.get("promote_ready"))
    promoted = bool(payload.get("promoted"))
    if extraction_gate:
        current_step = 0
    elif has_pending_disc or payload.get("review_kind") == "amount_discrepancy":
        current_step = 2
    elif promoted:
        current_step = 4
    elif promote_ready or comparable:
        current_step = 3
    elif todo_n == 0:
        current_step = 2
    else:
        current_step = 1

    def _step_cls(n: int) -> str:
        if extraction_gate:
            return "blocked" if n == 1 else ""
        # Pending amount discrepancies keep step 2 current even if names were already promoted.
        if has_pending_disc:
            if n == 1:
                return "done"
            if n == 2:
                return "current"
            return ""
        if promoted:
            return "done"
        if n < current_step:
            return "done"
        if n == current_step:
            return "current"
        return ""

    if extraction_gate:
        next_action = bi(
            "Now: inspect the candidate holdings page in the PDF preview, then mark engineer help if Cost/FV columns are unclear.",
            "现在：在右侧 PDF 预览看疑似持仓页；若 Cost/FV 列不清楚，点需要工程师帮忙。",
        )
        sticky_primary = f"""
      <button type="button" class="primary" data-action="ack-extract" data-ack="needs_engineer">{bi('Needs engineer help', '需要工程师帮忙')}</button>"""
        title = bi("PDF evidence review", "PDF 证据审核")
        hero = f"""
      <div class="card hero-todo">
        <h2>{bi('What are you deciding?', '你在决定什么？')}</h2>
        <p>{bi(
          'Only: did the system correctly understand the holdings table columns on this PDF?',
          '只问一件事：系统是否正确理解了这份 PDF 持仓表的列？'
        )}</p>
      </div>
      {gate_block}
        """
    elif payload.get("review_kind") == "amount_discrepancy" or (payload.get("discrepancy_pending") or []):
        next_action = bi(
            "Now: open each amount card, compare PDF vs Excel, click Show in PDF, then choose which value to write into reviewed_extraction.xlsx.",
            "现在：打开每张金额卡，对比 PDF 与 Excel，点「在 PDF 中查看」，再选择写入 reviewed_extraction.xlsx 的值。",
        )
        sticky_primary = ""
        title = bi("Amount discrepancy review", "金额差异审核")
        hero = f"""
      <div class="card hero-todo">
        <h2>{bi('What are you deciding?', '你在决定什么？')}</h2>
        <p>{bi(
          'PDF extraction and vendor Excel disagree for the same company/date. Choose the final value for our reviewed Excel. The vendor Excel is never modified.',
          '同一公司/报告日下，PDF 提取与第三方 Excel 不一致。为「已审核提取 Excel」选择最终值。绝不改第三方 Excel。'
        )}</p>
      </div>
        """
    elif extraction_ack.get("action") in {"auto_skipped_not_holdings", "skip_no_holdings_schedule"}:
        next_action = bi("Done. You can close this tab.", "已完成。可以关掉这个网页。")
        sticky_primary = ""
        title = bi("PDF triage done", "PDF 分流已完成")
        hero = f"""
      <div class="card success-zone">
        <h2 class="ok">{bi('✓ Decision saved', '✓ 判断已保存')}</h2>
        <p>{bi(
          f'This PDF was marked: {extraction_ack.get("action")}. No holdings name matching is required.',
          f'这份 PDF 已标记为：{extraction_ack.get("action")}。不需要做持仓名称匹配。'
        )}</p>
      </div>
        """
    elif promoted:
        next_action = bi(
            "Done. Name matches are saved. You can close this tab.",
            "已完成。名称对应已保存。可以关掉这个网页。",
        )
        sticky_primary = ""
        title = bi("Holdings name review", "持仓名称审批")
        hero = f"""
      <div class="card hero-todo">
        <h2>{bi('What do you need to review?', '你到底需要审核什么？')}</h2>
        <ol class="plain">
          <li>{bi('Only decide PDF schedule names that did NOT auto-match to an Excel Source Asset.', '只需处理「未能自动对到 Excel Source Asset」的 PDF 名称。')}</li>
          <li>{bi('You are NOT reviewing whether the business is good — only whether the name maps to the right Excel holding row.', '不是在审公司好坏，只是确认名字是否对应 Excel 同一行持仓。')}</li>
          <li>{bi('Auto-matched names (exact after dropping city suffixes like “(Austin, TX)”) need no action.', '已自动匹配的（去掉城市括号后完全一致）不用管。')}</li>
          <li>{bi('Primary amounts to trust: Current Cost ↔ PDF Cost, Unrealized Value ↔ PDF Fair Value.', '金额主看：Current Cost ↔ PDF Cost，Unrealized Value ↔ PDF Fair Value。')}</li>
        </ol>
        <div class="actions">
          <button type="button" data-action="bulk">{bi('Bulk-accept all auto-matched defaults', '批量接受全部自动匹配项')}</button>
        </div>
        <details class="help muted-details">
          <summary>{bi('Field dictionary (Excel ↔ PDF)', '字段词典（Excel ↔ PDF）')}</summary>
          <div class="body">
            <table class="amt">
              <thead><tr><th>Excel</th><th>PDF</th><th>{bi('Meaning', '含义')}</th></tr></thead>
              <tbody>{glossary_rows}</tbody>
            </table>
          </div>
        </details>
      </div>
        """
    elif current_step == 1:
        next_action = bi(
            "Now: finish the TODO cards below (accept a name, or mark “not an Excel holding”).",
            "现在：处理下面的待办卡片（确认 Excel 名称，或标成「不是 Excel 持仓」）。",
        )
        sticky_primary = ""
        title = bi("Holdings name review", "持仓名称审批")
        hero = f"""
      <div class="card hero-todo">
        <h2>{bi('What do you need to review?', '你到底需要审核什么？')}</h2>
        <ol class="plain">
          <li>{bi('Only decide PDF schedule names that did NOT auto-match to an Excel Source Asset.', '只需处理「未能自动对到 Excel Source Asset」的 PDF 名称。')}</li>
          <li>{bi('You are NOT reviewing whether the business is good — only whether the name maps to the right Excel holding row.', '不是在审公司好坏，只是确认名字是否对应 Excel 同一行持仓。')}</li>
          <li>{bi('Auto-matched names (exact after dropping city suffixes like “(Austin, TX)”) need no action.', '已自动匹配的（去掉城市括号后完全一致）不用管。')}</li>
          <li>{bi('Primary amounts to trust: Current Cost ↔ PDF Cost, Unrealized Value ↔ PDF Fair Value.', '金额主看：Current Cost ↔ PDF Cost，Unrealized Value ↔ PDF Fair Value。')}</li>
        </ol>
        <div class="actions">
          <button type="button" data-action="bulk">{bi('Bulk-accept all auto-matched defaults', '批量接受全部自动匹配项')}</button>
        </div>
        <details class="help muted-details">
          <summary>{bi('Field dictionary (Excel ↔ PDF)', '字段词典（Excel ↔ PDF）')}</summary>
          <div class="body">
            <table class="amt">
              <thead><tr><th>Excel</th><th>PDF</th><th>{bi('Meaning', '含义')}</th></tr></thead>
              <tbody>{glossary_rows}</tbody>
            </table>
          </div>
        </details>
      </div>
        """
    elif current_step == 2:
        next_action = bi(
            "Now: click “Check Cost / Fair Value” in the bottom bar.",
            "现在：点底部「核对 Cost / Fair Value」。",
        )
        sticky_primary = f"""
      <button type="button" id="compareBtn" class="primary" data-action="compare">{bi('Check Cost / Fair Value', '核对 Cost / Fair Value')}</button>"""
        title = bi("Holdings name review", "持仓名称审批")
        hero = f"""
      <div class="card hero-todo">
        <h2>{bi('What do you need to review?', '你到底需要审核什么？')}</h2>
        <ol class="plain">
          <li>{bi('Only decide PDF schedule names that did NOT auto-match to an Excel Source Asset.', '只需处理「未能自动对到 Excel Source Asset」的 PDF 名称。')}</li>
          <li>{bi('You are NOT reviewing whether the business is good — only whether the name maps to the right Excel holding row.', '不是在审公司好坏，只是确认名字是否对应 Excel 同一行持仓。')}</li>
          <li>{bi('Auto-matched names (exact after dropping city suffixes like “(Austin, TX)”) need no action.', '已自动匹配的（去掉城市括号后完全一致）不用管。')}</li>
          <li>{bi('Primary amounts to trust: Current Cost ↔ PDF Cost, Unrealized Value ↔ PDF Fair Value.', '金额主看：Current Cost ↔ PDF Cost，Unrealized Value ↔ PDF Fair Value。')}</li>
        </ol>
        <div class="actions">
          <button type="button" data-action="bulk">{bi('Bulk-accept all auto-matched defaults', '批量接受全部自动匹配项')}</button>
        </div>
        <details class="help muted-details">
          <summary>{bi('Field dictionary (Excel ↔ PDF)', '字段词典（Excel ↔ PDF）')}</summary>
          <div class="body">
            <table class="amt">
              <thead><tr><th>Excel</th><th>PDF</th><th>{bi('Meaning', '含义')}</th></tr></thead>
              <tbody>{glossary_rows}</tbody>
            </table>
          </div>
        </details>
      </div>
        """
    else:
        next_action = bi(
            "Now: type your name in Step 3, then click Save. Do not use the optional amount re-check unless you changed names.",
            "现在：在第 3 步填名字，再点保存。除非改过名称，否则不用再点金额核对。",
        )
        sticky_primary = f"""
      <button type="button" class="primary" data-action="promote">{bi('Save name matches for this fund', '保存本基金的名称对应')}</button>
      <button type="button" id="compareBtn" data-action="compare">{bi('Re-check Cost / Fair Value (optional)', '再核对一次金额（可选）')}</button>"""
        title = bi("Holdings name review", "持仓名称审批")
        hero = f"""
      <div class="card hero-todo">
        <h2>{bi('What do you need to review?', '你到底需要审核什么？')}</h2>
        <ol class="plain">
          <li>{bi('Only decide PDF schedule names that did NOT auto-match to an Excel Source Asset.', '只需处理「未能自动对到 Excel Source Asset」的 PDF 名称。')}</li>
          <li>{bi('You are NOT reviewing whether the business is good — only whether the name maps to the right Excel holding row.', '不是在审公司好坏，只是确认名字是否对应 Excel 同一行持仓。')}</li>
          <li>{bi('Auto-matched names (exact after dropping city suffixes like “(Austin, TX)”) need no action.', '已自动匹配的（去掉城市括号后完全一致）不用管。')}</li>
          <li>{bi('Primary amounts to trust: Current Cost ↔ PDF Cost, Unrealized Value ↔ PDF Fair Value.', '金额主看：Current Cost ↔ PDF Cost，Unrealized Value ↔ PDF Fair Value。')}</li>
        </ol>
        <div class="actions">
          <button type="button" data-action="bulk">{bi('Bulk-accept all auto-matched defaults', '批量接受全部自动匹配项')}</button>
        </div>
        <details class="help muted-details">
          <summary>{bi('Field dictionary (Excel ↔ PDF)', '字段词典（Excel ↔ PDF）')}</summary>
          <div class="body">
            <table class="amt">
              <thead><tr><th>Excel</th><th>PDF</th><th>{bi('Meaning', '含义')}</th></tr></thead>
              <tbody>{glossary_rows}</tbody>
            </table>
          </div>
        </details>
      </div>
        """

    names_section = ""
    if not extraction_gate and not (
        extraction_ack.get("action") in {"auto_skipped_not_holdings", "skip_no_holdings_schedule", "skip_not_comparable", "acknowledged"}
        and not needs
    ):
        names_section = f"""
      <section id="step-names" class="todo-zone">
        <h2>{bi(f'Name mapping TODO ({todo_n})', f'名称映射待办（{todo_n}）')}</h2>
        <p class="meta">{bi('You are only confirming whether a PDF company name is the same Excel Source Asset.', '你只是在确认 PDF 公司名是否对应同一个 Excel Source Asset。')}</p>
        {needs_cards if needs_cards else f'<div class="card"><span class="ok">{bi("Nothing left — all names auto-matched.", "没有待办 — 名称已全部自动匹配。")}</span></div>'}
      </section>

      <section class="auto-zone">
        <div class="auto-summary">
          {bi(f'{len(auto_done)} names already auto-matched — no review needed.', f'已有 {len(auto_done)} 个名称自动匹配 — 无需审核。')}
        </div>
        <details class="help muted-details">
          <summary>{bi('Show auto-matched list (optional check)', '查看已自动匹配列表（可选核对）')}</summary>
          <div class="body"><ul class="plain compact">{auto_list or f'<li>{bi("(none)", "（无）")}</li>'}</ul></div>
        </details>
      </section>
        """

    disc_items = payload.get("discrepancy_pending") or [
        i for i in (payload.get("discrepancy_items") or []) if i.get("needs_action")
    ]
    disc_section = ""
    if disc_items and not extraction_gate:
        disc_cards = "".join(discrepancy_card_html(i) for i in disc_items)
        disc_section = f"""
      <section id="step-discrepancy" class="todo-zone">
        <h2>{bi(f'Amount discrepancy TODO ({len(disc_items)})', f'金额差异待办（{len(disc_items)}）')}</h2>
        <p class="meta">{bi('Same company + same as-of date, but PDF and Excel amounts disagree. Decide what to write into reviewed_extraction.xlsx.', '同一公司 + 同一报告日，但 PDF 与 Excel 金额不一致。决定写入 reviewed_extraction.xlsx 的值。')}</p>
        {disc_cards}
      </section>
        """

    left_col = f"""
      {hero}
      <section id="step-amounts">{compare_block}</section>
      {disc_section}
      {names_section}
      {promote_block}
    """
    preview_col = preview_pane_html(payload)

    return f"""
    <header>
      <div class="header-row">
        <div>
          <h1>{title} · Fund {esc(payload.get('fund_id'))} · {esc(payload.get('as_of_date'))}</h1>
          <div class="meta">
            <span>{bi(route_ui.get('extraction_label_en') or '', route_ui.get('extraction_label') or '')}</span>
            <span class="counts">
              <span class="warn">{bi(f'TODO {summary_ui.get("todo_count", 0)}', f'待办 {summary_ui.get("todo_count", 0)}')}</span>
              <span class="warn">{bi(f'Discrepancy {summary_ui.get("discrepancy_pending", 0)}', f'金额差异 {summary_ui.get("discrepancy_pending", 0)}')}</span>
              <span class="ok">{bi(f'Auto {summary_ui.get("auto_count", 0)}', f'已自动 {summary_ui.get("auto_count", 0)}')}</span>
            </span>
          </div>
          <div class="meta">{bi('PDF reporting entity', 'PDF 报告主体')}: {esc(short_fund or '—')}</div>
          <div class="meta">{bi('Review kind', '审核类型')}: <b>{esc(payload.get('review_kind') or payload.get('open_reason') or '—')}</b></div>
          <ul class="step-nav" aria-label="progress">
            <li class="{_step_cls(1)}"><span class="n">1</span>{bi('Confirm names', '确认名称')}</li>
            <li class="{_step_cls(2)}"><span class="n">2</span>{bi('Check Cost / FV', '核对金额')}</li>
            <li class="{_step_cls(3)}"><span class="n">3</span>{bi('Save matches', '保存对应')}</li>
          </ul>
          <div class="next-action">
            <strong>{bi('What to click now', '现在该点什么')}</strong>
            {next_action}
          </div>
        </div>
        <div class="lang-switch">
          <button type="button" id="langEn" class="active" onclick="setLang('en')">EN</button>
          <button type="button" id="langZh" onclick="setLang('zh')">中文</button>
        </div>
      </div>
    </header>
    <main class="layout-split">
      <div class="left-col">{left_col}</div>
      {preview_col}
    </main>
    <div class="sticky-bar">
      {sticky_primary}
      <a class="btn" href="/api/payload">{bi('Debug JSON', '调试 JSON')}</a>
      <a class="btn" href="/api/review-items">{bi('Review items JSON', '审核项 JSON')}</a>
    </div>
    """
