import { test } from '@playwright/test';
import { evaluationJourney } from './evaluation-journey';

test('completed real evaluation results project exact episodes, recordings and phase rates', async ({ page, request, context }, info) => {
  await evaluationJourney('results', { page, request, context }, info);
});
