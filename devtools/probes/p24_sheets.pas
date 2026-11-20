procedure Body;
var S, It, SS, It2, Ch: Variant;
begin
  Client.ShowDocument(Client.OpenDocument('SCH', '%OUT%\e2e_complex\complex_hierarchy.SchDoc'));
  S := SchServer.GetSchDocumentByPath('%OUT%\e2e_complex\complex_hierarchy.SchDoc');
  if S = Nil then begin L('not open'); Exit; end;
  It := S.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eSheetSymbol));
  SS := It.FirstSchObject;
  while SS <> Nil do
  begin
    L('sheetsym name=[' + SS.SheetName.Text + '] file=[' + SS.SheetFileName.Text + '] uid=' + SS.UniqueId);
    It2 := SS.SchIterator_Create;
    Ch := It2.FirstSchObject;
    while Ch <> Nil do
    begin
      L('   child objid=' + IntToStr(Ch.ObjectId) + ' text=' );
      Ch := It2.NextSchObject;
    end;
    SS.SchIterator_Destroy(It2);
    SS := It.NextSchObject;
  end;
  S.SchIterator_Destroy(It);
end;
