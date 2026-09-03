import { apiPost, withLoading, renderMetrics, renderError } from '../api.js';
import { cloudResourceCard } from './cloudAiResources.js';
import { dataSourceNote } from './dataSourceNote.js';

export function randomForestView(container) {
  container.innerHTML = `
    <section class="card">
      <h2><i class="fa-solid fa-tree"></i> Random Forest</h2>
      <p class="desc">
        앙상블 트리 분류기로 고객 이탈 예측을 수행합니다.<br/>
        Ensemble tree classifier for churn prediction.
      </p>
      ${dataSourceNote('synthetic', '이 화면은 예제로 만든 합성 고객 이탈 데이터를 사용합니다. 실제 기업 데이터가 아닙니다.')}
      <div class="form-row">
        <div class="form-group">
          <label>테스트 비율 / Test Size</label>
          <input id="rfTest" type="number" step="0.1" value="0.3" min="0.1" max="0.5" />
        </div>
      </div>
      <button class="run" id="rfRun">▶ 평가 실행 / Run</button>
      <div id="rfResult"></div>
    </section>
    ${cloudResourceCard('random-forest')}
  `;

  container.querySelector('#rfRun').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const resultEl = container.querySelector('#rfResult');
    const payload = { test_size: Number(container.querySelector('#rfTest').value) };
    try {
      const result = await withLoading(btn, () => apiPost('/api/ml/random-forest', payload));
      resultEl.innerHTML =
        renderMetrics([['Accuracy', result.accuracy.toFixed(4)]]) +
        `<pre>${JSON.stringify(result, null, 2)}</pre>`;
    } catch (err) {
      resultEl.innerHTML = renderError(err.message);
    }
  });
}
