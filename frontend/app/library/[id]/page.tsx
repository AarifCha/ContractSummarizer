import ViewerClient from "@/components/ViewerClient";

export default async function ViewerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const fileId = Number(id);
  return <ViewerClient id={fileId} />;
}
