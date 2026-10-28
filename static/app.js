const $ = s => document.querySelector(s);
const tpl = $('#filtersTemplate');

function mountFilters() {
  $('#commonFilters').append(tpl.content.cloneNode(true));
}
mountFilters();

const resultSets = {};
let resultSetId = 0;

function params(extra = {}) {
  const p = new URLSearchParams(extra);
  document.querySelectorAll('#commonFilters input,#commonFilters select').forEach(x => {
    if (x.value) p.set(x.id, x.value);
  });
  return p;
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function formatCell(column, value) {
  return (column === 'Amount' || column === 'TotalAmount' || column === 'GroupTotal') &&
    value !== null && value !== ''
    ? Number(value).toLocaleString(undefined, { style: 'currency', currency: 'USD' })
    : esc(value);
}

function sameFlags(a, b) {
  const fa = a || [];
  const fb = b || [];
  if (fa.length !== fb.length) return false;
  return fa.every((x, i) => x.level === fb[i].level && x.label === fb[i].label);
}

function table(rows, notes = []) {
  if (!rows.length) return '<p class="empty">No matching transactions found.</p>';

  const setId = `set${++resultSetId}`;
  resultSets[setId] = rows;
  const cols = Object.keys(rows[0]).filter(c => !['_flags', 'follow_up_flags', 'SplitGroup', 'SplitGroupTotal', 'SplitControl'].includes(c));

  // Control 4/5/6 results can represent one split-purchase group with
  // multiple transaction rows. Keep the rows separate so every transaction
  // can be opened, but render ONE review-flag cell for the whole group.
  const groupKey = r => r.SplitGroup || r.SplitGroupId || '';
  const groupStarts = {};
  rows.forEach((r, i) => {
    const key = groupKey(r);
    if (!key) return;
    if (i === 0 || groupKey(rows[i - 1]) !== key) {
      let count = 1;
      while (i + count < rows.length && groupKey(rows[i + count]) === key) count++;
      groupStarts[i] = count;
    }
  });

  let body = '';
  rows.forEach((r, index) => {
    const flags = (r._flags && Array.isArray(r._flags))
      ? r._flags
      : (Array.isArray(r.follow_up_flags)
          ? r.follow_up_flags.map(label => ({
              level: 'review',
              label: label
            }))
          : []);
    const level = flags.some(f => f.level === 'high') ? 'high' : flags.length ? 'review' : '';
    const key = groupKey(r);
    const isGrouped = Boolean(key);
    const isGroupStart = Object.prototype.hasOwnProperty.call(groupStarts, index);

    let flagCell = '';
    if (!isGrouped || isGroupStart) {
      const rowspan = isGroupStart ? groupStarts[index] : 1;
      flagCell = `<td class="flagCell" style="vertical-align:top"${rowspan > 1 ? ` rowspan="${rowspan}"` : ''}>` +
        (flags.map(f => `<span class="flag ${esc(f.level)}">${esc(f.label)}</span>`).join('') ||
          '<span class="clear">No automatic flag</span>') +
        '</td>';
    }

    body += `<tr class="${level} selectable${isGrouped ? ' split-group-row' : ''}${isGroupStart ? ' split-group-start' : ''}"
                  tabindex="0" role="button"
                  data-result-set="${setId}" data-row-index="${index}">
              ${flagCell}
              ${cols.map(c => `<td>${formatCell(c, r[c])}</td>`).join('')}
            </tr>`;

    // Add one visible total row after every split-purchase group.
    // The individual transactions remain separate, but the reviewer can
    // immediately see the combined amount without doing the math.
    const isGroupEnd = isGrouped &&
      (index === rows.length - 1 || groupKey(rows[index + 1]) !== key);

    if (isGroupEnd) {
      let groupStart = index;
      while (groupStart > 0 && groupKey(rows[groupStart - 1]) === key) {
        groupStart--;
      }
      const groupRows = rows.slice(groupStart, index + 1);
      const amountColumn = cols.includes('Amount') ? 'Amount' :
        (cols.includes('TotalAmount') ? 'TotalAmount' :
        (cols.includes('CombinedAmount') ? 'CombinedAmount' : null));

      if (amountColumn) {
        const groupTotal = groupRows.reduce((sum, item) => {
          const n = Number(item[amountColumn]);
          return sum + (Number.isFinite(n) ? n : 0);
        }, 0);

        const amountIndex = cols.indexOf(amountColumn);
        const labelSpan = amountIndex > 0 ? amountIndex : 1;
        const trailing = cols.length - amountIndex - 1;

        body += `<tr class="split-total-row">
                  <td class="split-total-flag-space"></td>
                  <td colspan="${labelSpan}"><strong>${esc((groupRows[0].SplitControl || '') + ' GROUP TOTAL')}</strong></td>
                  <td><strong>${formatCell(amountColumn, groupTotal)}</strong></td>
                  ${trailing > 0 ? `<td colspan="${trailing}"></td>` : ''}
                </tr>`;
      }
    }
  });

  return `
    <div class="notes">${notes.map(x => `<span>${esc(x)}</span>`).join('')}</div>
    <p class="openHint">Select any result row to view its full details.</p>
    <div class="tablewrap"><table>
      <thead><tr>
        <th>Review flags</th>
        ${cols.map(c => `<th>${esc(c)}</th>`).join('')}
      </tr></thead>
      <tbody>${body}</tbody>
    </table></div>`;
}

function showDetail(row) {
  const flags = (row._flags && Array.isArray(row._flags))
    ? row._flags
    : (Array.isArray(row.follow_up_flags)
        ? row.follow_up_flags.map(label => ({
            level: 'review',
            label: label
          }))
        : []);
  const fields = Object.entries(row).filter(([key]) => !['_flags', 'SplitGroup', 'SplitGroupTotal', 'SplitControl'].includes(key));
  const flagHtml = flags.length
    ? `<div class="detailFlags">${flags.map(f => `<span class="flag ${esc(f.level)}">${esc(f.label)}</span>`).join('')}</div>`
    : '<p class="clear">No automatic review flag on this result.</p>';

  const panel = $('#transactionDetail');
  if (!panel) return;

  panel.innerHTML = `
    <div class="detailHeader"><h2>Transaction details</h2>
      <button id="closeDetail" aria-label="Close transaction details">Close</button>
    </div>
    ${flagHtml}
    <dl>${fields.map(([key, value]) =>
      `<div><dt>${esc(key)}</dt><dd>${formatCell(key, value)}</dd></div>`
    ).join('')}</dl>`;

  panel.hidden = false;
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  $('#closeDetail').onclick = () => { panel.hidden = true; };
}

function openSelectedRow(element) {
  const row = resultSets[element.dataset.resultSet]?.[Number(element.dataset.rowIndex)];
  if (row) showDetail(row);
}

document.addEventListener('click', event => {
  const row = event.target.closest('tr[data-result-set]');
  if (row) openSelectedRow(row);
});

document.addEventListener('keydown', event => {
  if ((event.key === 'Enter' || event.key === ' ') &&
      event.target.matches('tr[data-result-set]')) {
    event.preventDefault();
    openSelectedRow(event.target);
  }
});

async function getJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

async function run(target, status, extra = {}) {
  try {
    const u = '/api/search?' + params(extra);
    const d = await getJson(u);
    $(status).textContent = `${d.rows.length} transaction(s) returned (maximum 1,000).`;
    $(target).innerHTML = table(d.rows, d.match_notes || []);
  } catch (error) {
    $(status).textContent = error.message;
    $(target).innerHTML = '';
  }
}

async function showSafeSearchOptions() {
  const q = $('#question').value.trim();
  if (!q) {
    $('#suggestions').innerHTML = '<p class="warning">Enter a search idea first.</p>';
    return;
  }

  try {
    const d = await getJson('/api/suggest?q=' + encodeURIComponent(q));
    $('#suggestions').innerHTML =
      `<p class="warning">${esc(d.warning)}</p>` +
      d.suggestions.map(s =>
        `<button type="button" class="suggest" data-f="${esc(s.field)}" data-t="${esc(s.term)}">
          Search ${esc(s.label)} for “${esc(s.term)}”
        </button>`
      ).join('');

    document.querySelectorAll('.suggest').forEach(b => {
      b.onclick = async () => {
        $('#field').value = b.dataset.f;
        $('#term').value = b.dataset.t;
        await run('#exploreResults', '#exploreStatus');
      };
    });
  } catch (error) {
    $('#suggestions').innerHTML = `<p class="warning">${esc(error.message)}</p>`;
  }
}

$('#exploreBtn').onclick = () => run('#exploreResults', '#exploreStatus');

$('#descBtn').onclick = () => {
  const term = $('#descTerm').value.trim();
  $('#term').value = term;
  $('#field').value = 'Description';
  run('#prohibitedResults', '#prohibitedStatus');
};

$('#vendorBtn').onclick = () => {
  const term = $('#vendorTerm').value.trim();
  $('#term').value = term;
  $('#field').value = 'Vendor';
  run('#prohibitedResults', '#prohibitedStatus');
};

$('#suggestBtn').onclick = showSafeSearchOptions;

$('#question').addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    showSafeSearchOptions();
  }
});

$('#term').addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    run('#exploreResults', '#exploreStatus');
  }
});

$('#descTerm').addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    $('#descBtn').click();
  }
});

$('#vendorTerm').addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    $('#vendorBtn').click();
  }
});

$('#aiPreset').onchange = event => {
  if (event.target.value) $('#aiQuestion').value = event.target.value;
};

$('#aiSolveBtn').onclick = async () => {
  const question = $('#aiQuestion').value.trim() || $('#aiPreset').value;
  if (!question) {
    $('#aiStatus').textContent = 'Choose or enter an assignment question first.';
    return;
  }

  $('#aiStatus').textContent = 'AI is preparing a read-only local query…';
  $('#aiResults').innerHTML = '';

  try {
    const data = await getJson('/api/ai/solve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question })
    });

    $('#aiStatus').textContent = `${data.rows.length} row(s) returned.`;
    $('#aiResults').innerHTML =
      `<h4>${esc(data.title)}</h4>` +
      `<p>${esc(data.explanation)}</p>` +
      `<details><summary>Show AI-proposed SQL</summary><pre>${esc(data.sql)}</pre></details>` +
      table(data.rows, data.match_notes || []);
  } catch (error) {
    $('#aiStatus').textContent = error.message;
  }
};

document.querySelectorAll('[data-check]').forEach(button => {
  button.onclick = async () => {
    const year = $('#year')?.value || '2014';
    const check = button.dataset.check;

    $('#controlStatus').textContent = 'Running audit check…';

    try {
      const data = await getJson(
        `/api/control-check?check=${encodeURIComponent(check)}&year=${encodeURIComponent(year)}`
      );
      $('#controlStatus').textContent = `${data.rows.length} potential item(s) returned.`;
      $('#controlResults').innerHTML =
        `<h4>${esc(data.title)}</h4>` +
        table(data.rows, data.match_notes || []);
    } catch (error) {
      $('#controlStatus').textContent = error.message;
      $('#controlResults').innerHTML = '';
    }
  };
});

document.querySelectorAll('.tab').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('.tab,.panel').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    $('#' + b.dataset.tab).classList.add('active');
  };
});