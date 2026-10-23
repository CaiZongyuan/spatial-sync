import { test } from '@playwright/test';
import { evaluationJourney } from './evaluation-journey';

test('held critical alert banner stays read only through its own completed evaluation episode', async ({ page, request, context }, info) => {
  await evaluationJourney('banner', { page, request, context }, info);
});
