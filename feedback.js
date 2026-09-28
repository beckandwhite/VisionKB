"use strict";
function initFeedback() {
  const el = document.getElementById("tab-feedback");
  if (!el || el.dataset.ready) return;
  el.dataset.ready = "1";
  el.innerHTML = `
<div class="feedback-content">
  <section class="feedback-section">
    <h2 class="feedback-heading">About VisionKB</h2>
    <p class="feedback-body">VisionKB extracts text, tags, and entities from screenshots and images to build a searchable local knowledge base powered by local vision models.</p>
    <a class="feedback-link" href="https://github.com/beckandwhite/VisionKB" target="_blank" rel="noopener">View on GitHub →</a>
  </section>
  <section class="feedback-section">
    <h2 class="feedback-heading">Report a Bug</h2>
    <p class="feedback-body">Found something broken? Open a pre-filled bug report on GitHub.</p>
    <a class="feedback-link feedback-link--cta" href="https://github.com/beckandwhite/VisionKB/issues/new?template=bug_report.md" target="_blank" rel="noopener">Report a Bug →</a>
  </section>
  <section class="feedback-section">
    <h2 class="feedback-heading">Request a Feature</h2>
    <p class="feedback-body">Have an idea for VisionKB? Open a feature request on GitHub.</p>
    <a class="feedback-link" href="https://github.com/beckandwhite/VisionKB/issues/new" target="_blank" rel="noopener">Request a Feature →</a>
  </section>
  <section class="feedback-section">
    <h2 class="feedback-heading">Contributing</h2>
    <p class="feedback-body">Read the README for setup instructions and project overview.</p>
    <a class="feedback-link" href="https://github.com/beckandwhite/VisionKB/blob/main/README.md" target="_blank" rel="noopener">Read the README →</a>
  </section>
</div>`;
}
