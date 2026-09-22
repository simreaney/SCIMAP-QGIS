/* SCIMAP dashboard — charts, drawn as hand-built SVG.
 *
 * SVG rather than canvas or a charting library: it prints at printer
 * resolution (this doubles as an evidence document), gives hit-testing for
 * tooltips for free, and keeps the vendored payload to Leaflet and three.js.
 *
 * Colours follow core/plotting.py so the dashboard and the plugin's exported
 * PNGs look like one product.
 */
(function (global) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';

  /* Chart furniture follows the page theme, read from the same CSS custom
   * properties everything else uses. Earlier this was a fixed light palette
   * with a CSS invert filter over the whole figure in dark mode — which also
   * inverted the ramp colours, so a Magma histogram read light-to-dark while
   * the legend beside it read dark-to-light. Data colours must never be
   * filtered. */
  var INK, MUTED, FAINT, GRID, ACCENT;

  function readTheme() {
    var style = getComputedStyle(document.documentElement);
    function token(name, fallback) {
      return (style.getPropertyValue(name) || '').trim() || fallback;
    }
    INK = token('--ink', '#0b0b0b');
    MUTED = token('--ink-soft', '#52514e');
    FAINT = token('--ink-faint', '#898781');
    GRID = token('--edge', '#e1e0d9');
    ACCENT = token('--accent', '#2a78d6');
  }

  function el(name, attributes, text) {
    var node = document.createElementNS(NS, name);
    Object.keys(attributes || {}).forEach(function (key) {
      node.setAttribute(key, attributes[key]);
    });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function frame(width, height, padding) {
    var svg = el('svg', {
      viewBox: '0 0 ' + width + ' ' + height,
      preserveAspectRatio: 'xMidYMid meet',
      class: 'chart'
    });
    return {
      svg: svg, width: width, height: height, pad: padding,
      plotWidth: width - padding.left - padding.right,
      plotHeight: height - padding.top - padding.bottom
    };
  }

  function axes(chart, xLabel, yLabel, xTicks, yTicks) {
    var pad = chart.pad;
    var group = el('g', {});

    yTicks.forEach(function (tick) {
      var y = pad.top + chart.plotHeight * (1 - tick.at);
      group.appendChild(el('line', {
        x1: pad.left, x2: pad.left + chart.plotWidth, y1: y, y2: y,
        stroke: GRID, 'stroke-width': 1
      }));
      group.appendChild(el('text', {
        x: pad.left - 8, y: y + 4, 'text-anchor': 'end',
        fill: MUTED, 'font-size': 11
      }, tick.label));
    });

    xTicks.forEach(function (tick) {
      var x = pad.left + chart.plotWidth * tick.at;
      group.appendChild(el('text', {
        x: x, y: chart.height - pad.bottom + 16, 'text-anchor': 'middle',
        fill: MUTED, 'font-size': 11
      }, tick.label));
    });

    group.appendChild(el('line', {
      x1: pad.left, x2: pad.left + chart.plotWidth,
      y1: pad.top + chart.plotHeight, y2: pad.top + chart.plotHeight,
      stroke: FAINT, 'stroke-width': 1
    }));

    if (xLabel) {
      group.appendChild(el('text', {
        x: pad.left + chart.plotWidth / 2, y: chart.height - 8,
        'text-anchor': 'middle', fill: INK, 'font-size': 11.5
      }, xLabel));
    }
    if (yLabel) {
      group.appendChild(el('text', {
        x: 12, y: pad.top + chart.plotHeight / 2, 'text-anchor': 'middle',
        fill: INK, 'font-size': 11.5,
        transform: 'rotate(-90 12 ' + (pad.top + chart.plotHeight / 2) + ')'
      }, yLabel));
    }
    chart.svg.appendChild(group);
    return chart;
  }

  function niceTicks(min, max, count) {
    var ticks = [];
    for (var i = 0; i <= count; i++) {
      var at = i / count;
      ticks.push({at: at, label: global.SCIMAP.formatValue(min + at * (max - min), 2)});
    }
    return ticks;
  }

  // ── histogram ───────────────────────────────────────────────────────────

  function histogram(container, layer) {
    readTheme();
    var data = ((global.SCIMAP_CHARTS || {}).histograms || {})[layer.key];
    container.innerHTML = '';
    if (!data || !data.counts.length) {
      container.appendChild(note('No distribution available for this layer.'));
      return;
    }

    var chart = frame(560, 252, {top: 14, right: 14, bottom: 46, left: 54});
    var peak = Math.max.apply(null, data.counts) || 1;
    var barWidth = chart.plotWidth / data.counts.length;
    var lo = data.edges[0], hi = data.edges[data.edges.length - 1];

    axes(chart, layer.label + (layer.units ? ' (' + layer.units + ')' : ''), 'Cells',
         niceTicks(lo, hi, 4),
         [{at: 0, label: '0'}, {at: 0.5, label: fmtCount(peak / 2)}, {at: 1, label: fmtCount(peak)}]);

    // Shade the 5-95 percentile band the colour stretch actually uses.
    if (layer.stretch) {
      var from = (layer.stretch.low - lo) / (hi - lo);
      var to = (layer.stretch.high - lo) / (hi - lo);
      chart.svg.appendChild(el('rect', {
        x: chart.pad.left + chart.plotWidth * Math.max(0, from), y: chart.pad.top,
        width: chart.plotWidth * Math.min(1, to - from), height: chart.plotHeight,
        fill: ACCENT, opacity: 0.07
      }));
    }

    var group = el('g', {});
    data.counts.forEach(function (count, index) {
      var height = (count / peak) * chart.plotHeight;
      var fraction = index / Math.max(data.counts.length - 1, 1);
      var bar = el('rect', {
        x: chart.pad.left + index * barWidth,
        y: chart.pad.top + chart.plotHeight - height,
        width: Math.max(barWidth - 0.6, 0.6), height: height,
        fill: global.SCIMAP.rampColour(global.SCIMAP.rampFor(layer.key), fraction)
      });
      bar.appendChild(el('title', {}, global.SCIMAP.formatValue(data.edges[index], 3) +
        ' to ' + global.SCIMAP.formatValue(data.edges[index + 1], 3) +
        ': ' + count.toLocaleString() + ' cells'));
      group.appendChild(bar);
    });
    chart.svg.appendChild(group);
    container.appendChild(chart.svg);
  }

  function fmtCount(value) {
    if (value >= 1e6) return (value / 1e6).toFixed(1) + 'M';
    if (value >= 1e3) return (value / 1e3).toFixed(0) + 'k';
    return Math.round(value).toString();
  }

  // ── wetness-connectivity scatter ────────────────────────────────────────

  function scatter(container) {
    readTheme();
    var data = (global.SCIMAP_CHARTS || {}).scatter;
    container.innerHTML = '';
    if (!data || !data.points || !data.points.length) {
      container.appendChild(note(
        'No wetness-connectivity curve in this export. Run SCIMAP Network Index ' +
        'with the curve dataset output enabled to include one.'));
      return;
    }

    var xs = data.points.map(function (p) { return p[0]; });
    var ys = data.points.map(function (p) { return p[1]; });
    var xMin = Math.min.apply(null, xs), xMax = Math.max.apply(null, xs);
    var yMin = Math.min.apply(null, ys), yMax = Math.max.apply(null, ys);
    var xSpan = (xMax - xMin) || 1, ySpan = (yMax - yMin) || 1;

    var chart = frame(560, 312, {top: 14, right: 14, bottom: 46, left: 54});
    axes(chart, data.x || 'Topographic wetness index', data.y || 'Connectivity',
         niceTicks(xMin, xMax, 4), niceTicks(yMin, yMax, 4));

    // Bin to a grid and shade by density: 4000 overplotted dots read as a blob,
    // and the shape of this cloud is the whole point of the chart.
    var cols = 150, rows = 90;
    var bins = new Uint32Array(cols * rows);
    data.points.forEach(function (point) {
      var cx = Math.min(cols - 1, Math.floor(((point[0] - xMin) / xSpan) * cols));
      var cy = Math.min(rows - 1, Math.floor(((point[1] - yMin) / ySpan) * rows));
      bins[cy * cols + cx]++;
    });
    var peak = 0;
    for (var i = 0; i < bins.length; i++) if (bins[i] > peak) peak = bins[i];

    var group = el('g', {});
    var cellW = chart.plotWidth / cols, cellH = chart.plotHeight / rows;
    for (var row = 0; row < rows; row++) {
      for (var col = 0; col < cols; col++) {
        var count = bins[row * cols + col];
        if (!count) continue;
        // sqrt keeps sparse tails visible next to a dense core.
        var weight = Math.sqrt(count / peak);
        group.appendChild(el('rect', {
          x: chart.pad.left + col * cellW,
          y: chart.pad.top + chart.plotHeight - (row + 1) * cellH,
          width: cellW + 0.4, height: cellH + 0.4,
          fill: ACCENT, opacity: (0.12 + 0.88 * weight).toFixed(3)
        }));
      }
    }
    chart.svg.appendChild(group);
    container.appendChild(chart.svg);

    var caption = document.createElement('p');
    caption.className = 'chart-note';
    caption.textContent = 'Showing ' + data.shown.toLocaleString() + ' of ' +
      data.total.toLocaleString() + ' sampled cells, shaded by density.';
    container.appendChild(caption);
  }

  // ── risk concentration across the landscape ─────────────────────────────

  /* A Lorenz curve over every cell of the catchment, not just the channel
   * network: land ranked lowest risk to highest against the share of total
   * risk it carries. The diagonal is perfectly even risk; the further the
   * curve sags below it, the more a small area dominates. */
  function concentration(container, layer) {
    readTheme();
    var all = (global.SCIMAP_CHARTS || {}).concentration || {};
    var data = layer ? all[layer.key] : null;
    container.innerHTML = '';
    if (!data || !data.x || data.x.length < 2) {
      container.appendChild(note('No concentration curve available for this layer.'));
      return;
    }

    var chart = frame(560, 312, {top: 14, right: 14, bottom: 46, left: 54});
    axes(chart, 'Share of the catchment, lowest risk first (%)',
         'Share of total risk (%)',
         [{at: 0, label: '0'}, {at: 0.25, label: '25'}, {at: 0.5, label: '50'},
          {at: 0.75, label: '75'}, {at: 1, label: '100'}],
         [{at: 0, label: '0'}, {at: 0.5, label: '50'}, {at: 1, label: '100'}]);

    var pad = chart.pad;
    // The line of perfectly even risk, for the curve to be read against.
    chart.svg.appendChild(el('line', {
      x1: pad.left, y1: pad.top + chart.plotHeight,
      x2: pad.left + chart.plotWidth, y2: pad.top,
      stroke: FAINT, 'stroke-width': 1.5, 'stroke-dasharray': '5 4'
    }));

    var points = data.x.map(function (x, i) {
      return [pad.left + chart.plotWidth * x, pad.top + chart.plotHeight * (1 - data.y[i])];
    });
    var path = points.map(function (p) { return p[0] + ',' + p[1]; }).join('L');
    chart.svg.appendChild(el('path', {
      d: 'M' + pad.left + ',' + (pad.top + chart.plotHeight) + 'L' + path +
         'L' + (pad.left + chart.plotWidth) + ',' + (pad.top + chart.plotHeight) + 'Z',
      fill: ACCENT, opacity: 0.12
    }));
    chart.svg.appendChild(el('path', {
      d: 'M' + path, fill: 'none', stroke: ACCENT, 'stroke-width': 2.4
    }));

    // Mark the worst 5% of land, which is what the headline quotes.
    var markX = pad.left + chart.plotWidth * 0.95;
    chart.svg.appendChild(el('line', {
      x1: markX, y1: pad.top, x2: markX, y2: pad.top + chart.plotHeight,
      stroke: ACCENT, 'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: 0.55
    }));

    var readout = document.createElement('div');
    readout.className = 'chart-readout';
    readout.textContent = 'Hover the curve to read any share.';
    var hit = el('rect', {
      x: pad.left, y: pad.top, width: chart.plotWidth, height: chart.plotHeight,
      fill: 'transparent', style: 'cursor:crosshair'
    });
    var dot = el('circle', {r: 4, fill: ACCENT, opacity: 0});
    chart.svg.appendChild(hit);
    chart.svg.appendChild(dot);

    hit.addEventListener('mousemove', function (event) {
      // The SVG scales to its container, so map the pointer back through the
      // 560-unit viewBox rather than trusting on-screen pixels.
      var box = chart.svg.getBoundingClientRect();
      var px = (event.clientX - box.left) * (560 / box.width);
      var fraction = Math.max(0, Math.min(1, (px - pad.left) / chart.plotWidth));
      var index = Math.round(fraction * (data.x.length - 1));
      var landPct = data.x[index] * 100, riskPct = data.y[index] * 100;
      dot.setAttribute('cx', points[index][0]);
      dot.setAttribute('cy', points[index][1]);
      dot.setAttribute('opacity', 1);
      readout.textContent =
        'The least risky ' + landPct.toFixed(0) + '% of the catchment holds ' +
        riskPct.toFixed(0) + '% of the risk — so the remaining ' +
        (100 - landPct).toFixed(0) + '% produces ' + (100 - riskPct).toFixed(0) + '%.';
    });
    hit.addEventListener('mouseleave', function () {
      dot.setAttribute('opacity', 0);
      readout.textContent = 'Hover the curve to read any share.';
    });

    container.appendChild(chart.svg);

    if (data.headline) {
      var caption = document.createElement('p');
      caption.className = 'chart-headline';
      caption.textContent = data.headline;
      container.appendChild(caption);
    }
    container.appendChild(readout);

    // The table is what gets quoted in a report, so give both readings of the
    // question: area first, and risk first.
    var table = document.createElement('table');
    table.className = 'concentration-table';
    var head = '<thead><tr><th>Highest-risk share of the catchment</th>' +
               '<th>Risk it produces</th></tr></thead><tbody>';
    (data.worst || []).forEach(function (row) {
      head += '<tr><td>top ' + row.landPct + '%</td><td>' + row.riskPct + '%</td></tr>';
    });
    head += '</tbody>';
    table.innerHTML = head;
    container.appendChild(table);

    var table2 = document.createElement('table');
    table2.className = 'concentration-table';
    var head2 = '<thead><tr><th>To capture this much risk</th>' +
                '<th>You must treat this much land</th></tr></thead><tbody>';
    (data.needed || []).forEach(function (row) {
      head2 += '<tr><td>' + row.riskPct + '% of risk</td><td>' +
               row.landPct + '% of the catchment</td></tr>';
    });
    head2 += '</tbody>';
    table2.innerHTML = head2;
    container.appendChild(table2);

    var foot = document.createElement('p');
    foot.className = 'chart-caption';
    foot.textContent = 'Over ' + data.cells.toLocaleString() + ' cells' +
      (data.areaKm2 ? ' (' + data.areaKm2.toLocaleString() + ' km\u00b2)' : '') +
      '. Gini coefficient ' + data.gini +
      ' — 0 is risk spread perfectly evenly, 1 is all of it in one place.';
    container.appendChild(foot);
  }

  // ── priority reaches ────────────────────────────────────────────────────

  function topReaches(container, onSelect) {
    var rows = (global.SCIMAP_CHARTS || {}).topReaches || [];
    container.innerHTML = '';
    if (!rows.length) {
      container.appendChild(note('No ranked reaches in this export.'));
      return;
    }

    var peak = rows[0].risk || 1;
    var table = document.createElement('table');
    table.className = 'reach-table';
    table.innerHTML = '<thead><tr><th>#</th><th>Risk</th><th>Length</th>' +
                      '<th class="bar-col">Relative risk</th></tr></thead>';
    var body = document.createElement('tbody');

    rows.forEach(function (row, index) {
      var tr = document.createElement('tr');
      tr.tabIndex = 0;
      tr.innerHTML =
        '<td>' + (index + 1) + '</td>' +
        '<td>' + global.SCIMAP.formatValue(row.risk, 4) + '</td>' +
        '<td>' + Math.round(row.lengthM).toLocaleString() + ' m</td>' +
        '<td class="bar-col"><span class="bar" style="width:' +
          Math.max(2, (row.risk / peak) * 100).toFixed(1) + '%;background:' +
          global.SCIMAP.rampColour('plasma', row.risk / peak) + '"></span></td>';
      function go() { if (onSelect) onSelect(row); }
      tr.addEventListener('click', go);
      tr.addEventListener('keydown', function (e) { if (e.key === 'Enter') go(); });
      body.appendChild(tr);
    });

    table.appendChild(body);
    container.appendChild(table);
  }

  function note(text) {
    var p = document.createElement('p');
    p.className = 'chart-note';
    p.textContent = text;
    return p;
  }

  readTheme();

  global.SCIMAP_CHARTS_UI = {
    histogram: histogram, scatter: scatter, concentration: concentration,
    topReaches: topReaches, note: note, readTheme: readTheme
  };
})(window);
