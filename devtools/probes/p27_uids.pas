procedure Body;
var D, S, It, C: Variant;
begin
  D := Client.OpenDocument('SCH', '%OUT%\batch\pic_programmer\pic_programmer.SchDoc');
  Client.ShowDocument(D);
  S := SchServer.GetSchDocumentByPath('%OUT%\batch\pic_programmer\pic_programmer.SchDoc');
  It := S.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eSchComponent));
  C := It.FirstSchObject;
  while C <> Nil do
  begin
    if (C.Designator.Text = 'U2') or (C.Designator.Text = 'U3') then
      L(C.Designator.Text + ' part=' + IntToStr(C.CurrentPartID) + ' uid=' + C.UniqueId + ' libref=' + C.LibReference);
    C := It.NextSchObject;
  end;
  S.SchIterator_Destroy(It);
  Client.CloseDocument(D);
end;
