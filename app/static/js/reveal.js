
(function () {
  if (!("IntersectionObserver" in window)) return;

  document.documentElement.classList.add("js-reveal");

  var targets = document.querySelectorAll(".reveal");
  if (!targets.length) return;

  var observer = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("reveal-in");
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.15, rootMargin: "0px 0px -40px 0px" }
  );

  targets.forEach(function (el, i) {
    
    el.style.transitionDelay = Math.min(i % 4, 3) * 70 + "ms";
    observer.observe(el);
  });
})();
