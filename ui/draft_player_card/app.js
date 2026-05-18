(function () {
  const data = window.DRAFT_PROSPECT_CARD;
  const prospect = data.prospect;
  const read = data.read;
  const workout = data.workout;

  const $ = (id) => document.getElementById(id);

  function setText(id, value) {
    $(id).textContent = value || "--";
  }

  function gradeClass(value) {
    if (value == null) return "empty";
    if (value >= 80) return "excellent";
    if (value >= 70) return "strong";
    if (value >= 60) return "steady";
    return "raw";
  }

  function renderMetric(metric) {
    const value = metric.value;
    const card = document.createElement("div");
    card.className = `metric-card ${gradeClass(value)}`;

    const top = document.createElement("div");
    top.className = "metric-top";

    const label = document.createElement("span");
    label.textContent = metric.label;

    const score = document.createElement("strong");
    score.textContent = value == null ? "--" : Math.round(value);

    top.append(label, score);

    const bar = document.createElement("div");
    bar.className = "bar";
    bar.style.setProperty("--value", `${Math.max(0, Math.min(100, value || 0))}%`);

    const bottom = document.createElement("p");
    bottom.textContent = metric.detail ? `${metric.grade} | ${metric.detail}` : metric.grade;

    card.append(top, bar, bottom);
    return card;
  }

  function renderList(id, values) {
    const list = $(id);
    list.replaceChildren();
    values.forEach((value) => {
      const item = document.createElement("li");
      item.textContent = value;
      list.append(item);
    });
  }

  function init() {
    $("portraitImage").src = data.portrait;
    $("portraitImage").alt = `${prospect.name} portrait`;

    setText("draftYear", `${prospect.draftYear} Draft`);
    setText("draftRank", `Board #${prospect.rank}`);
    setText("collegeLine", prospect.college);
    setText("prospectName", prospect.name);
    setText("positionLine", `${prospect.position} | ${prospect.archetype}`);
    setText("classLine", `${prospect.hometown || prospect.birthCountry} | ${prospect.ethnicity}`);
    setText("archetypeLine", `${prospect.position} ${prospect.archetype}`);
    setText("pickText", `R${prospect.projectedRound} P${prospect.projectedPick}`);

    setText("ageText", prospect.age);
    setText("heightText", prospect.height);
    setText("weightText", prospect.weight);
    setText("armText", prospect.arm);
    setText("handText", prospect.hand);

    const metricBoard = $("metricBoard");
    metricBoard.replaceChildren();
    data.metrics.forEach((metric) => metricBoard.append(renderMetric(metric)));

    setText("riskText", `${read.risk} risk`);
    setText("reportText", read.report);
    renderList("strengthList", data.strengths);
    renderList("concernList", data.concerns);

    const secondary = prospect.secondaryRole ? ` / ${prospect.secondaryRole}` : "";
    setText("roleText", `${prospect.primaryRole}${secondary}`);
    setText("lensText", `${read.lens} (${read.confidence})`);
    setText("combineText", `${workout.combineStatus}: ${workout.combineSummary || workout.combineNote}`);
    setText("appearanceText", prospect.appearance);
  }

  init();
}());
