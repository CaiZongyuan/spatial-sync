import { test } from '@playwright/test';
import { evaluationJourney } from './evaluation-journey';

test('held evaluation workspace and alerts stay read only through their own completed episode', async ({ page, request, context }, info) => {
  await evaluationJourney('readonly', { page, request, context }, info);
});
