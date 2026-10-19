"""A shared light canvas and accessible controls for the two vis-network views."""


def finish_network(html: str) -> str:
    style = """
    <style>
    html,body{margin:0;background:#fff;font-family:Arial,sans-serif;overflow:hidden}
    center{display:none}
    .card{border:0!important;background:#fff;border-radius:6px!important}
    #mynetwork{border:1px solid #E1E8E0!important;border-radius:6px;background-color:#fff!important;
        background-image:radial-gradient(#DCE5DC 0.7px,transparent 0.7px);background-size:19px 19px}
    .graph-tools{position:absolute;left:13px;bottom:12px;display:flex;gap:4px;z-index:3}
    .graph-tools button{width:31px;height:31px;border:1px solid #DDE5DD;border-radius:5px;background:#fff;
        color:#5F7767;font-size:18px;cursor:pointer;box-shadow:0 1px 3px #26382f08}
    .graph-tools button:hover{background:#EDF3EB}.graph-tools button:focus-visible{outline:2px solid #356B54}
    .vis-tooltip{font:12px/1.6 Arial,sans-serif!important;color:#334D3E!important;background:#fff!important;
        border:1px solid #DDE5DD!important;border-radius:5px!important;box-shadow:0 4px 12px #26382f12!important}
    </style>
    """
    controls = """
    <div class="graph-tools" aria-label="Graph controls">
      <button type="button" title="Zoom in" aria-label="Zoom in" onclick="network.moveTo({scale:network.getScale()*1.25})">+</button>
      <button type="button" title="Zoom out" aria-label="Zoom out" onclick="network.moveTo({scale:network.getScale()/1.25})">&minus;</button>
      <button type="button" title="Fit graph" aria-label="Fit graph" onclick="network.fit({animation:false})">&#8644;</button>
    </div>
    <script>
      network.once('stabilizationIterationsDone', function () {
        network.fit({animation:false});
        network.setOptions({physics:false});
      });
      window.addEventListener('resize', function () {network.fit({animation:false});});
    </script>
    """
    return html.replace("</head>", style + "</head>").replace("</body>", controls + "</body>")
