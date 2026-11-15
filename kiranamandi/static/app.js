// Commodity tabs on the home page. The page works without this script: the first panel shows.
document.querySelectorAll('[data-tabs]').forEach(function (group) {
  var buttons = group.querySelectorAll('[data-tab]');
  buttons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      buttons.forEach(function (b) {
        var selected = b === btn;
        b.setAttribute('aria-pressed', selected ? 'true' : 'false');
        var panel = document.getElementById(b.getAttribute('data-tab'));
        if (panel) { panel.hidden = !selected; }
      });
    });
  });
});
