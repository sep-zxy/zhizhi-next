import { syncGrowthCloud, type GrowthCloudAccess } from '../api/growth';

/** No extra project publication approval or forced history refresh; initial bootstrap/outbox sync is unchanged. */
export async function connectLearningTopics(cloudUrl: string, token: string): Promise<GrowthCloudAccess> {
  const cloud_url = cloudUrl.trim();
  const access_token = token.trim();
  if (!cloud_url || !access_token) throw new Error('请填写既有学习服务地址与访问令牌。');
  const summary = await syncGrowthCloud(cloud_url, access_token, undefined, false);
  if (typeof summary?.account_id !== 'string' || !summary.account_id.trim()) {
    throw new Error('服务未确认学习账号，不能打开主题管理。');
  }
  return { cloud_url, access_token, account_id: summary.account_id };
}
