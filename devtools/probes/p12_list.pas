procedure Body;
var IL: Variant; N, Board, Doc: Variant;
begin
  try
    IL := TInterfaceList.Create;
    L('TInterfaceList created');
    Doc := CreateNewDocumentFromDocumentKind('PCB');
    Board := PCBServer.GetCurrentPCBBoard;
    N := PCBServer.PCBObjectFactory(eNetObject, eNoDimension, eCreate_Default);
    N.Name := 'NETA';
    Board.AddPCBObject(N);
    IL.Add(N);
    N := Nil;
    N := IL.Items[0];
    L('got back: ' + N.Name + ' count=' + IntToStr(IL.Count));
    Client.CloseDocument(Doc);
  except
    L('failed: ' + ExceptionMessage);
  end;
end;
