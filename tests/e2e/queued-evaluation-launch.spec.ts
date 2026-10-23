import { test } from '@playwright/test';
import { evaluationJourney } from './evaluation-journey';

test('queued launches retain cancellation and hand off after real evaluation grading', async ({ page, request, context }, info) => {
  await evaluationJourney('handoff', { page, request, context }, info);
});
